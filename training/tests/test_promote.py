from contextlib import redirect_stderr, redirect_stdout
import io
import subprocess
import sys
import unittest

from gomoku_training.promote import command


class PublicationCommandTests(unittest.TestCase):
    def test_hidden_command_preserves_and_optionally_relays_both_streams(self):
        script = "import sys; print('result'); print('diagnostic', file=sys.stderr)"
        for capture in (False, True):
            with self.subTest(capture=capture):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    result = command(sys.executable, "-c", script, capture=capture)
                self.assertEqual(result.stdout, "result\n")
                self.assertEqual(result.stderr, "diagnostic\n")
                self.assertEqual(out.getvalue(), "" if capture else "result\n")
                self.assertEqual(err.getvalue(), "" if capture else "diagnostic\n")

    def test_failure_keeps_diagnostics_in_exception_and_log(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(subprocess.CalledProcessError) as caught:
            command(sys.executable, "-c", "import sys; print('push failed', file=sys.stderr); sys.exit(7)",
                    capture=True)
        self.assertEqual(caught.exception.returncode, 7)
        self.assertEqual(caught.exception.stderr, "push failed\n")
        self.assertEqual(err.getvalue(), "push failed\n")

    def test_unicode_build_output_does_not_fail_on_legacy_windows_console(self):
        data = io.BytesIO()
        stream = io.TextIOWrapper(data, encoding="gbk")
        with redirect_stdout(stream):
            result = command(sys.executable, "-c", "import sys; sys.stdout.buffer.write('\\u2713'.encode('utf-8'))")
        self.assertEqual(result.stdout, "\u2713")
        self.assertEqual(data.getvalue(), b"\\u2713")
        stream.close()


if __name__ == "__main__":
    unittest.main()
