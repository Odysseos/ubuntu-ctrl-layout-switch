"""Physical key selection and configurable modifier mappings."""

import io
from unittest.mock import Mock, call, patch

from test_ctrl_layout import EXTERNAL, INTERNAL, IsolatedCase, app


class KeySelectionTests(IsolatedCase):
    def pair(self, key, device=EXTERNAL):
        return f"Keyboard {device} {key} down\nKeyboard {device} {key} up\n"

    def capture(self, events):
        process = Mock(stdout=io.StringIO(events))
        with patch.object(app.subprocess, "Popen", return_value=process):
            try:
                return app.capture_layout_keys("/usr/bin/keyd")
            finally:
                process.terminate.assert_called_once()
                process.wait.assert_called_once()

    def test_select_shift_then_alt(self):
        result = self.capture(self.pair("rightshift") + self.pair("leftalt"))
        self.assertEqual(result, (EXTERNAL, ("rightshift", "leftalt")))

    def test_duplicate_second_key_requires_another_key(self):
        result = self.capture(self.pair("leftmeta") * 2 + self.pair("rightalt"))
        self.assertEqual(result[1], ("leftmeta", "rightalt"))

    def test_duplicate_alone_cannot_complete_selection(self):
        with self.assertRaisesRegex(RuntimeError, "двух клавиш"):
            self.capture(self.pair("rightshift") * 2)

    def test_unsupported_key_is_ignored(self):
        result = self.capture(
            self.pair("a") + self.pair("leftshift") + self.pair("rightmeta")
        )
        self.assertEqual(result[1], ("leftshift", "rightmeta"))

    def test_release_without_press_is_ignored(self):
        events = f"Keyboard {EXTERNAL} leftshift up\n"
        result = self.capture(events + self.pair("leftalt") + self.pair("rightalt"))
        self.assertEqual(result[1], ("leftalt", "rightalt"))

    def test_second_key_on_another_device_is_ignored(self):
        result = self.capture(
            self.pair("leftshift")
            + self.pair("leftalt", INTERNAL)
            + self.pair("rightshift")
        )
        self.assertEqual(result, (EXTERNAL, ("leftshift", "rightshift")))

    def test_chord_does_not_choose_a_key(self):
        events = "".join(
            f"Keyboard {EXTERNAL} {key} {event}\n"
            for key, event in (
                ("leftshift", "down"),
                ("leftalt", "down"),
                ("leftshift", "up"),
                ("leftalt", "up"),
            )
        )
        result = self.capture(events + self.pair("rightshift") + self.pair("rightalt"))
        self.assertEqual(result[1], ("rightshift", "rightalt"))

    def test_fn_is_ignored_during_selection(self):
        result = self.capture(
            self.pair("fn") + self.pair("leftalt") + self.pair("rightshift")
        )
        self.assertEqual(result[1], ("leftalt", "rightshift"))

    def test_fn_cannot_be_written_to_config(self):
        with self.assertRaises(ValueError):
            app.build_keyd_config([EXTERNAL], ("fn", "rightshift"))

    def test_cancel_restores_service(self):
        with patch.object(
            app, "capture_layout_keys", side_effect=KeyboardInterrupt
        ), self.assertRaises(KeyboardInterrupt):
            app.choose_layout_keys()
        self.assertEqual(
            self.commands.call_args_list[-1], call("sudo", "systemctl", "start", "keyd")
        )

    def test_all_supported_distinct_pairs_round_trip(self):
        for first in app.KEY_LAYERS:
            for second in app.KEY_LAYERS:
                if first == second:
                    continue
                with self.subTest(first=first, second=second):
                    config = app.build_keyd_config(
                        [EXTERNAL, INTERNAL], (first, second)
                    )
                    self.assertEqual(
                        app.parse_keyd_config(config),
                        ([EXTERNAL, INTERNAL], (first, second)),
                    )

    def test_same_key_cannot_be_written_to_config(self):
        with self.assertRaises(ValueError):
            app.build_keyd_config([EXTERNAL], ("leftalt", "leftalt"))

    def test_right_alt_preserves_altgr(self):
        config = app.build_keyd_config([EXTERNAL], ("rightalt", "leftmeta"))
        self.assertIn("rightalt = overload(altgr, f13)", config)
        self.assertIn("leftmeta = overload(meta, f14)", config)

    def test_wrong_modifier_layer_is_rejected(self):
        config = app.build_keyd_config([EXTERNAL], ("rightalt", "leftmeta"))
        with self.assertRaises(RuntimeError):
            app.parse_keyd_config(config.replace("overload(altgr,", "overload(alt,"))
