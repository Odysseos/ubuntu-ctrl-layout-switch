"""Exercise the real controlling-terminal dialog, not a mock of ask_more."""

import errno
import os
import pty
import select
import unittest

from test_ctrl_layout import app


class TerminalDialog(unittest.TestCase):
    def dialog(self, answers, expected, hint="[д/Н]"):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                app.confirmation_hint = lambda: hint
                print("RESULT=" + str(app.ask_add_keyboard()), flush=True)
                os._exit(0)
            except BaseException:
                import traceback

                traceback.print_exc()
                os._exit(1)
        output = b""
        pending = iter(answers)
        prompt = ("Добавить ещё клавиатуру? " + hint + ": ").encode()
        seen = 0
        try:
            while True:
                # Test watchdog only: no delay in the implementation.
                ready, _, _ = select.select([fd], [], [], 5)
                self.assertTrue(ready, "Dialog stopped producing events")
                try:
                    chunk = os.read(fd, 4096)
                except OSError as e:
                    if e.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output += chunk
                if output.count(prompt) > seen:
                    seen += 1
                    os.write(fd, (next(pending) + "\n").encode())
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
            self.assertIn(("RESULT=" + str(expected)).encode(), output)
        finally:
            os.close(fd)
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass

    def test_english_yes(self):
        self.dialog(["y"], True, "[y/N]")

    def test_english_no(self):
        self.dialog(["n"], False, "[y/N]")

    def test_english_in_russian(self):
        self.dialog(["YES"], True)

    def test_russian_in_english(self):
        self.dialog(["нет"], False, "[y/N]")

    def test_unknown_layout(self):
        self.dialog(["n"], False, "[y/N · д/Н]")

    def test_yes(self):
        self.dialog(["д"], True)

    def test_default_no(self):
        self.dialog([""], False)

    def test_retry_then_no(self):
        self.dialog(["wrong", "н"], False)


if __name__ == "__main__":
    unittest.main()
