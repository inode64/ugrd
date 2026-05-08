from os import fsdecode
from subprocess import CompletedProcess, TimeoutExpired
from unittest import TestCase, main
from unittest.mock import Mock, patch

from ugrd.generator_helpers import GeneratorHelpers


class TestGeneratorHelpers(TestCase):
    def _get_helper(self):
        helper = GeneratorHelpers()
        helper.logger = Mock()
        helper.timeout = 1
        return helper

    def test_run_error_logging_handles_non_utf8_output(self):
        """Ensure failed command logging does not mask errors with UnicodeDecodeError."""
        cmd = CompletedProcess(["bad-command"], 1, b"salida-\xe9\n", b"fallo-\xf3\n")
        helper = self._get_helper()

        with patch("ugrd.generator_helpers.run", return_value=cmd):
            with self.assertRaises(RuntimeError):
                helper._run(["bad-command"])

        helper.logger.error.assert_any_call("Command output:\n%s" % fsdecode(cmd.stdout))
        helper.logger.error.assert_any_call("Command error:\n%s" % fsdecode(cmd.stderr))

    def test_run_timeout_logging_handles_non_utf8_output(self):
        """Ensure timeout logging does not mask errors with UnicodeDecodeError."""
        timeout = TimeoutExpired(["slow-command"], 1, output=b"salida-\xe9\n", stderr=b"fallo-\xf3\n")
        helper = self._get_helper()

        with patch("ugrd.generator_helpers.run", side_effect=timeout):
            with self.assertRaises(RuntimeError):
                helper._run(["slow-command"])

        helper.logger.error.assert_any_call("Command output:\n%s" % fsdecode(timeout.stdout))
        helper.logger.error.assert_any_call("Command error:\n%s" % fsdecode(timeout.stderr))


if __name__ == "__main__":
    main()
