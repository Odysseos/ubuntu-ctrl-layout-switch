"""Regression tests. No root access, GNOME session, or running keyd required."""

import base64
import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

SCRIPT = Path(__file__).resolve().parents[1] / "ctrl-layout.py"
spec = importlib.util.spec_from_file_location("ctrl_layout", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
EXTERNAL = "046d:b35b:794355b5"
INTERNAL = "0001:0001:09b4e68d"


class FakeSettings:
    def __init__(self):
        self.values = {"sources": [("xkb", "us"), ("xkb", "ru")], "xkb-options": []}
        self.locked = set()

    def get_value(self, key):
        return types.SimpleNamespace(unpack=lambda: self.values[key])

    def is_writable(self, key):
        return key not in self.locked

    def set_value(self, key, value):
        if key in self.locked:
            return False
        self.values[key] = value.unpack()
        return True


class IsolatedCase(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.settings = FakeSettings()
        self.state = self.root / "state"
        self.state.mkdir()
        self.config = self.root / "keyd" / "ctrl-layout.conf"
        self.config.parent.mkdir()
        self.xkb = self.root / "xkb"
        self.gio = types.SimpleNamespace(
            Settings=types.SimpleNamespace(
                new=Mock(return_value=self.settings), sync=Mock()
            ),
            BusType=types.SimpleNamespace(SESSION=0),
            bus_get_sync=Mock(),
        )
        for name, value in {
            "state_directory": self.state,
            "state_file": self.state / "state.json",
            "xkb_directory": self.xkb,
            "keyd_config_path": self.config,
            "test_state_directory": self.root / "old-test",
            "settings": self.settings,
            "Gio": self.gio,
        }.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        # Unmocked process creation is a test failure, never a real system action.
        self.stack.enter_context(
            patch.object(
                app.subprocess,
                "Popen",
                side_effect=AssertionError("Unexpected process"),
            )
        )
        self.status = self.stack.enter_context(
            patch.object(app.subprocess, "run", side_effect=self.status_command)
        )
        self.commands = self.stack.enter_context(patch.object(app, "run_command"))
        self.stack.enter_context(
            patch.object(app, "find_keyd_binary", return_value="/usr/bin/keyd")
        )

    @staticmethod
    def status_command(args, **kwargs):
        if args[:2] not in (["systemctl", "is-active"], ["systemctl", "is-enabled"]):
            raise AssertionError(f"Unexpected command: {args}")
        return subprocess.CompletedProcess(args, 0)

    def record(self):
        return {
            "version": 2,
            "config": app.build_keyd_config([EXTERNAL]),
            "config_before": None,
            "files_before": {name: None for name in app.FILES},
            "options_before": [],
            "options": ["grp:menu_toggle", app.OPTION],
            "sources_before": [["xkb", "us"], ["xkb", "ru"]],
            "service_enabled": True,
            "service_active": True,
            "enabled": False,
        }

    def use_local_config_writer(self):
        def write(path, value):
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(value)

        self.stack.enter_context(
            patch.object(app, "write_config_file", side_effect=write)
        )


class ConfigurationTests(IsolatedCase):
    def test_multiple_ids_round_trip(self):
        self.assertEqual(
            app.parse_keyboard_ids(app.build_keyd_config([EXTERNAL, INTERNAL])),
            [EXTERNAL, INTERNAL],
        )

    def test_duplicate_ids_normalized(self):
        self.assertEqual(
            app.parse_keyboard_ids(app.build_keyd_config([EXTERNAL.upper(), EXTERNAL])),
            [EXTERNAL],
        )

    def test_foreign_mapping_and_wildcard_rejected(self):
        for text in (
            app.build_keyd_config(["*"]),
            app.build_keyd_config([EXTERNAL]).replace("f13", "escape"),
        ):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                app.parse_keyboard_ids(text)

    def test_atomic_state_save(self):
        record = self.record()
        app.save_state(record)
        self.assertEqual(json.loads(app.state_file.read_text()), record)
        self.assertFalse((self.state / "state.tmp").exists())
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_each_external_change_is_rejected(self):
        for kind in ("config", "files", "options", "sources"):
            with self.subTest(kind=kind):
                if kind == "config":
                    self.config.write_text("user config")
                elif kind == "files":
                    app.write_xkb_files({"rules/evdev": "user rules"})
                elif kind == "options":
                    self.settings.values["xkb-options"] = ["compose:ralt"]
                else:
                    self.settings.values["sources"] = [("xkb", "de")]
                with self.assertRaises(RuntimeError):
                    app.check_managed_state(self.record())
                self.config.unlink(missing_ok=True)
                app.write_xkb_files({name: None for name in app.FILES})
                self.settings.values = {
                    "sources": [("xkb", "us"), ("xkb", "ru")],
                    "xkb-options": [],
                }
        self.commands.assert_not_called()

    def test_locked_setting_fails_before_changes(self):
        self.settings.locked.add("sources")
        with self.assertRaises(RuntimeError):
            app.enable(self.record())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.xkb.exists())
        self.commands.assert_not_called()


class LifecycleTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.use_local_config_writer()
        self.stack.enter_context(
            patch.object(
                app, "choose_layout_keys", return_value=(EXTERNAL, app.DEFAULT_KEYS)
            )
        )
        self.validation = self.stack.enter_context(patch.object(app, "validate_xkb"))

    def test_enable_disable_reenable_uninstall(self):
        record = self.record()
        app.enable(record)
        self.assertEqual(self.config.read_text(), record["config"])
        self.assertEqual(self.settings.values["xkb-options"], record["options"])
        app.disable(record)
        app.disable(record)  # Idempotent disabling.
        self.assertFalse(self.config.exists())
        app.enable(record)
        app.disable(record, uninstall=True)
        self.assertFalse(app.state_file.exists())
        self.assertTrue((self.state / "removed.json").exists())
        self.assertEqual(self.settings.values["xkb-options"], [])
        self.assertTrue(all(not (self.xkb / name).exists() for name in app.FILES))

    def test_add_keyboard_preserves_original_backup(self):
        record = self.record()
        record["config_before"] = "original"
        self.config.write_text("original")
        app.enable(record)
        expanded = dict(record, config=app.build_keyd_config([EXTERNAL, INTERNAL]))
        app.enable(expanded, previous=record)
        self.assertEqual(
            json.loads(app.state_file.read_text())["config_before"], "original"
        )
        app.disable(expanded, uninstall=True)
        self.assertEqual(self.config.read_text(), "original")

    def test_validation_failure_restores_files_and_settings(self):
        self.validation.side_effect = RuntimeError("bad keymap")
        with self.assertRaisesRegex(RuntimeError, "bad keymap"):
            app.enable(self.record())
        self.assertFalse(self.config.exists())
        self.assertFalse(app.state_file.exists())
        self.assertEqual(self.settings.values["xkb-options"], [])
        self.assertTrue(all(not (self.xkb / name).exists() for name in app.FILES))

    def test_reload_failure_rolls_back_after_config_written(self):
        record = self.record()
        record["config_before"] = "original"
        self.config.write_text("original")
        failed = False

        def command(*args):
            nonlocal failed
            if args[-1] == "reload" and not failed:
                failed = True
                raise subprocess.CalledProcessError(1, args)

        self.commands.side_effect = command
        with self.assertRaises(subprocess.CalledProcessError):
            app.enable(record)
        self.assertEqual(self.config.read_text(), "original")
        self.assertEqual(self.settings.values["xkb-options"], [])
        self.assertFalse(app.state_file.exists())

    def test_other_configuration_keeps_service_running(self):
        record = self.record()
        app.enable(record)
        (self.config.parent / "other.conf").write_text("unrelated")
        self.commands.reset_mock()
        app.disable(record)
        self.assertIn(
            call("sudo", "/usr/bin/keyd", "reload"), self.commands.call_args_list
        )
        self.assertNotIn(
            call("sudo", "systemctl", "stop", "keyd"), self.commands.call_args_list
        )

    def test_initial_installation(self):
        with patch.object(
            app,
            "configure_keyboards",
            return_value={
                EXTERNAL: app.DEFAULT_KEYS,
                INTERNAL: ("leftcontrol", "rightshift"),
            },
        ):
            record, config = app.prepare_installation()
        self.assertIsNone(record["config_before"])
        self.assertEqual(list(config), [EXTERNAL, INTERNAL])
        self.assertEqual(json.loads(app.state_file.read_text()), record)

    def test_adopt_installation_without_history(self):
        original = app.build_keyd_config([EXTERNAL])
        self.config.write_text(original)
        app.write_xkb_files(app.FILES)
        self.settings.values["xkb-options"] = ["grp:menu_toggle", app.OPTION]
        with patch.object(
            app,
            "configure_keyboards",
            return_value={
                EXTERNAL: app.DEFAULT_KEYS,
                INTERNAL: ("leftcontrol", "rightshift"),
            },
        ):
            record, config = app.prepare_installation()
        self.assertEqual(record["config_before"], original)
        self.assertEqual(record["files_before"], app.FILES)
        self.assertEqual(record["options"].count(app.OPTION), 1)

    def test_migrate_active_test_preserves_original_baseline(self):
        original = app.build_keyd_config([EXTERNAL])
        self.config.write_text(original)
        app.write_xkb_files(app.FILES)
        self.settings.values["xkb-options"] = ["grp:menu_toggle", app.OPTION]
        app.test_state_directory.mkdir()
        (app.test_state_directory / "backup.json").write_text(
            json.dumps(
                {
                    "config_before": base64.b64encode(original.encode()).decode(),
                    "config_after": original,
                    "options_before": [],
                    "options_after": self.settings.values["xkb-options"],
                }
            )
        )
        with patch.object(
            app,
            "configure_keyboards",
            return_value={
                EXTERNAL: app.DEFAULT_KEYS,
                INTERNAL: ("leftcontrol", "rightshift"),
            },
        ):
            record, config = app.prepare_installation()
        self.assertEqual(record["options_before"], [])
        self.assertTrue(all(value is None for value in record["files_before"].values()))
        self.assertEqual(list(config), [EXTERNAL, INTERNAL])

    def test_main_enable_does_not_collect_keyboards(self):
        app.save_state(self.record())
        with patch.object(app.os, "geteuid", return_value=1000), patch.object(
            app, "check_environment"
        ), patch.object(
            app, "configure_keyboards", side_effect=AssertionError("Unexpected prompt")
        ):
            app.main(["--enable"])
        self.assertTrue(self.config.exists())

    def test_main_unknown_action_and_root_do_not_run_commands(self):
        for uid, args in ((0, []), (1000, ["unknown"])):
            with self.subTest(uid=uid), patch.object(
                app.os, "geteuid", return_value=uid
            ), self.assertRaises(SystemExit):
                app.main(args)
        self.commands.assert_not_called()
        self.gio.bus_get_sync.assert_not_called()


class HintTests(unittest.TestCase):
    def test_hint_never_queries_xwayland(self):
        with patch.object(
            app.ctypes, "CDLL", side_effect=AssertionError("Unexpected layout query")
        ):
            self.assertEqual(app.confirmation_hint(), "[y/N · д/Н]")
            self.assertEqual(app.confirmation_hint(), "[y/N · д/Н]")


class XkbIntegrationTests(IsolatedCase):
    def test_real_keymap_compiles_and_passes_all_four_cases(self):
        app.write_xkb_files(app.FILES)
        with patch.dict(app.os.environ, {"XDG_CONFIG_HOME": str(self.root)}):
            app.validate_xkb(self.record())
        self.commands.assert_not_called()

    def test_incorrect_conditional_symbol_is_detected(self):
        files = dict(app.FILES)
        files["symbols/ctrl_layout"] = files["symbols/ctrl_layout"].replace(
            "VoidSymbol", "ISO_Next_Group"
        )
        app.write_xkb_files(files)
        with patch.dict(
            app.os.environ, {"XDG_CONFIG_HOME": str(self.root)}
        ), self.assertRaisesRegex(RuntimeError, "условного"):
            app.validate_xkb(self.record())


if __name__ == "__main__":
    unittest.main()
