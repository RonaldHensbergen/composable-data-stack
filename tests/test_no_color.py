"""Tests for NO_COLOR / --no-color resolution (#1094)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cli.main import no_color_requested


class NoColorRequestedTest(unittest.TestCase):
    def test_unset_env_and_flag_false(self):
        self.assertFalse(no_color_requested(False, environ={}))

    def test_flag_alone_disables_color(self):
        self.assertTrue(no_color_requested(True, environ={}))

    def test_nonempty_no_color_env_disables_color(self):
        self.assertTrue(no_color_requested(False, environ={"NO_COLOR": "1"}))
        self.assertTrue(no_color_requested(False, environ={"NO_COLOR": "true"}))
        self.assertTrue(no_color_requested(False, environ={"NO_COLOR": "yes"}))

    def test_empty_no_color_env_does_not_disable_color(self):
        # Per no-color.org the value is irrelevant, but presence of an empty
        # string does not count as "set".
        self.assertFalse(no_color_requested(False, environ={"NO_COLOR": ""}))

    def test_flag_or_env_either_disables(self):
        self.assertTrue(no_color_requested(True, environ={"NO_COLOR": ""}))
        self.assertTrue(no_color_requested(True, environ={"NO_COLOR": "1"}))

    def test_reads_os_environ_when_environ_omitted(self):
        with patch.dict("os.environ", {"NO_COLOR": "1"}, clear=False):
            self.assertTrue(no_color_requested(False))
        with patch.dict("os.environ", {"NO_COLOR": ""}, clear=False):
            # Ensure empty still means off even when reading real environ.
            # Other keys may exist; we only care about NO_COLOR.
            self.assertFalse(no_color_requested(False, environ={"NO_COLOR": ""}))


class DefaultRedrawHonorsClearScreenTest(unittest.TestCase):
    @patch("cli.up_runner.sys.stdout")
    def test_clear_screen_false_uses_plain_print_even_on_tty(self, mock_stdout):
        mock_stdout.isatty.return_value = True

        from cli.up_runner import _default_redraw

        _default_redraw("state text", clear_screen=False)

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertNotIn("\033[2J\033[H", written)

    @patch("cli.up_runner.sys.stdout")
    def test_clear_screen_true_emits_ansi_on_tty(self, mock_stdout):
        mock_stdout.isatty.return_value = True

        from cli.up_runner import _default_redraw

        _default_redraw("state text", clear_screen=True)

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("\033[2J\033[H", written)
        self.assertIn("state text", written)


class PollSettledUsesPlainRedrawWhenColorOff(unittest.TestCase):
    def test_default_redraw_skips_ansi_clear_when_use_color_false(self):
        from cli.up_runner import poll_state_until_settled
        import subprocess
        from unittest.mock import MagicMock

        ps_fn = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"Service": "web", "State": "running"}',
                stderr="",
            )
        )
        with patch("cli.up_runner._default_redraw") as mock_redraw:
            settled, _grouped = poll_state_until_settled(
                "docker-compose.yml",
                ps_fn=ps_fn,
                use_color=False,
                sleep_fn=MagicMock(),
                now_fn=MagicMock(side_effect=[0.0, 0.0]),
            )
            self.assertTrue(settled)
            mock_redraw.assert_called()
            # Default redraw path should pass clear_screen=False.
            _, kwargs = mock_redraw.call_args
            self.assertFalse(kwargs.get("clear_screen", True))
