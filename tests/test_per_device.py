"""Independent keyboard mappings and migration from a shared configuration."""

import json
import subprocess
from unittest.mock import patch

from test_ctrl_layout import EXTERNAL, INTERNAL, IsolatedCase, app


class PerDeviceTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.use_local_config_writer()
        self.stack.enter_context(patch.object(app, "validate_xkb"))
        self.profiles = {
            EXTERNAL: app.DEFAULT_KEYS,
            INTERNAL: ("leftcontrol", "rightshift"),
        }

    def migrated(self):
        old = self.record()
        old["config"] = app.build_keyd_config([EXTERNAL, INTERNAL])
        old["config_before"] = app.build_keyd_config([EXTERNAL])
        self.config.write_text(old["config"])
        return old, app.with_keyboard_profiles(old, self.profiles)

    def test_separate_files_and_round_trip_lifecycle(self):
        old, new = self.migrated()
        app.enable(new, old)
        self.assertFalse(self.config.exists())
        for device, keys in self.profiles.items():
            path = self.config.parent / (
                "ctrl-layout-" + device.replace(":", "-") + ".conf"
            )
            self.assertEqual(app.parse_keyd_config(path.read_text()), ([device], keys))
        app.disable(new)
        self.assertEqual(list(self.config.parent.glob("*.conf")), [])
        app.enable(new)
        app.disable(new, uninstall=True)
        self.assertEqual(self.config.read_text(), old["config_before"])
        self.assertEqual(list(self.config.parent.glob("*.conf")), [self.config])

    def test_edit_one_device_keeps_other_mapping(self):
        with patch.object(
            app,
            "choose_layout_keys",
            return_value=(INTERNAL, ("leftalt", "rightshift")),
        ), patch.object(app, "ask_add_keyboard", return_value=False):
            result = app.configure_keyboards(self.profiles)
        self.assertEqual(result[EXTERNAL], app.DEFAULT_KEYS)
        self.assertEqual(result[INTERNAL], ("leftalt", "rightshift"))
        self.assertEqual(self.profiles[INTERNAL], ("leftcontrol", "rightshift"))

    def test_two_devices_choose_different_pairs(self):
        with patch.object(
            app,
            "choose_layout_keys",
            side_effect=[
                (EXTERNAL, app.DEFAULT_KEYS),
                (INTERNAL, ("leftalt", "rightshift")),
            ],
        ), patch.object(app, "ask_add_keyboard", side_effect=[True, False]):
            result = app.configure_keyboards({})
        self.assertEqual(
            result, {EXTERNAL: app.DEFAULT_KEYS, INTERNAL: ("leftalt", "rightshift")}
        )

    def test_failed_reload_restores_all_files(self):
        old, new = self.migrated()
        failed = False

        def command(*args):
            nonlocal failed
            if args[-1] == "reload" and not failed:
                failed = True
                raise subprocess.CalledProcessError(1, args)

        self.commands.side_effect = command
        with self.assertRaises(subprocess.CalledProcessError):
            app.enable(new, old)
        self.assertEqual(self.config.read_text(), old["config"])
        self.assertEqual(list(self.config.parent.glob("*.conf")), [self.config])

    def test_filename_collision_is_not_overwritten(self):
        old, new = self.migrated()
        name = next(name for name in new["configs"] if name != self.config.name)
        path = self.config.parent / name
        path.write_text("unrelated")
        with self.assertRaisesRegex(RuntimeError, "не принадлежит"):
            app.enable(new, old)
        self.assertEqual(path.read_text(), "unrelated")
        self.assertEqual(self.config.read_text(), old["config"])

    def test_repeated_update_retains_original_backup(self):
        old, new = self.migrated()
        app.enable(new, old)
        updated = app.with_keyboard_profiles(
            new,
            {EXTERNAL: ("leftshift", "rightshift"), INTERNAL: self.profiles[INTERNAL]},
        )
        app.enable(updated, new)
        self.assertEqual(updated["configs_before"], new["configs_before"])
        app.disable(updated, uninstall=True)
        self.assertEqual(self.config.read_text(), old["config_before"])

    def test_main_migrates_existing_record(self):
        old, _ = self.migrated()
        app.save_state(old)
        with patch.object(app.os, "geteuid", return_value=1000), patch.object(
            app, "check_environment"
        ), patch.object(app, "configure_keyboards", return_value=self.profiles):
            app.main(["install"])
        saved = json.loads(app.state_file.read_text())
        self.assertEqual(saved["version"], 3)
        self.assertEqual(app.keyboard_profiles(saved), self.profiles)
        self.assertEqual(
            saved["configs_before"][self.config.name], old["config_before"]
        )
