import io
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cli.up_runner import (
    default_log_path,
    poll_state_until_settled,
    run_streamed,
    start_log_tail,
    stop_log_tail,
)


def _ps(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class DefaultLogPathTest(unittest.TestCase):
    def test_builds_expected_filename_shape(self):
        logs_dir = Path("/tmp/cds-logs")
        path = default_log_path("local-dagster-postgres-superset", logs_dir=logs_dir)
        self.assertEqual(path.parent, logs_dir)
        self.assertTrue(path.name.startswith("up-local-dagster-postgres-superset-"))
        self.assertTrue(path.name.endswith(".log"))

    def test_flattens_path_like_profile_names(self):
        logs_dir = Path("/tmp/cds-logs")
        path = default_log_path("profiles/my profile", logs_dir=logs_dir)
        self.assertNotIn("/", path.name)
        self.assertNotIn(" ", path.name)

    def test_defaults_to_dot_cds_logs_dir(self):
        path = default_log_path("demo")
        self.assertEqual(path.parent, Path(".cds") / "logs")


class RunStreamedTest(unittest.TestCase):
    def test_timeout_kills_a_hung_process(self):
        started = time.monotonic()
        returncode = run_streamed(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            io.StringIO(),
            echo=False,
            timeout=0.05,
        )

        self.assertEqual(returncode, 124)
        self.assertLess(time.monotonic() - started, 2)

    @patch("cli.up_runner.subprocess.Popen")
    def test_writes_output_to_log_file_and_returns_exit_code(self, mock_popen):
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter(["line one\n", "line two\n"])
        process.wait.return_value = 0
        mock_popen.return_value = process

        log_file = io.StringIO()
        returncode = run_streamed(["docker", "compose", "build"], log_file, echo=False)

        self.assertEqual(returncode, 0)
        self.assertEqual(log_file.getvalue(), "line one\nline two\n")

    @patch("cli.up_runner.subprocess.Popen")
    def test_propagates_nonzero_exit_code(self, mock_popen):
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter([])
        process.wait.return_value = 9
        mock_popen.return_value = process

        returncode = run_streamed(["docker", "compose", "build"], io.StringIO(), echo=False)

        self.assertEqual(returncode, 9)

    @patch("cli.up_runner.sys.stdout")
    @patch("cli.up_runner.subprocess.Popen")
    def test_group_by_image_emits_section_headers(self, mock_popen, mock_stdout):
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter([
            "web: Building 0.5s\n",
            "#1 [web 1/2] FROM node:18-alpine\n",
            "#1 DONE 0.5s\n",
            "db: Building 0.3s\n",
            "#2 [db 1/1] FROM nginx:alpine\n",
            "#2 DONE 0.3s\n",
        ])
        process.wait.return_value = 0
        mock_popen.return_value = process

        log_file = io.StringIO()
        returncode = run_streamed(["docker", "compose", "build"], log_file, echo=True, group_by_image=True)

        self.assertEqual(returncode, 0)
        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list if "Building" in call.args[0])
        self.assertIn("── Building web ", written)
        self.assertIn("── Building db ", written)
        self.assertIn("web: Building 0.5s\n", log_file.getvalue())
        self.assertIn("db: Building 0.3s\n", log_file.getvalue())

    @patch("cli.up_runner.sys.stdout")
    @patch("cli.up_runner.subprocess.Popen")
    def test_group_by_image_omits_ansi_codes_on_a_non_tty(self, mock_popen, mock_stdout):
        mock_stdout.isatty.return_value = False
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter(["web: Building 0.5s\n"])
        process.wait.return_value = 0
        mock_popen.return_value = process

        log_file = io.StringIO()
        run_streamed(["docker", "compose", "build"], log_file, echo=True, group_by_image=True)

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("── Building web ", written)
        self.assertNotIn("\033[", written)

    @patch("cli.up_runner.sys.stdout")
    @patch("cli.up_runner.subprocess.Popen")
    def test_group_by_image_omits_ansi_codes_when_color_disabled(self, mock_popen, mock_stdout):
        mock_stdout.isatty.return_value = True
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter(["web: Building 0.5s\n"])
        process.wait.return_value = 0
        mock_popen.return_value = process

        log_file = io.StringIO()
        run_streamed(
            ["docker", "compose", "build"], log_file, echo=True, group_by_image=True, use_color=False
        )

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("── Building web ", written)
        self.assertNotIn("\033[", written)

    @patch("cli.up_runner.sys.stdout")
    @patch("cli.up_runner.subprocess.Popen")
    def test_group_by_image_keeps_ansi_codes_on_a_tty_with_color(self, mock_popen, mock_stdout):
        mock_stdout.isatty.return_value = True
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter(["web: Building 0.5s\n"])
        process.wait.return_value = 0
        mock_popen.return_value = process

        log_file = io.StringIO()
        run_streamed(["docker", "compose", "build"], log_file, echo=True, group_by_image=True)

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("\033[36m── Building web ", written)

    @patch("cli.up_runner.sys.stdout")
    @patch("cli.up_runner.subprocess.Popen")
    def test_group_by_image_skips_headers_when_echo_is_off(self, mock_popen, mock_stdout):
        process = MagicMock()
        process.stdout = MagicMock()
        process.stdout.__iter__.return_value = iter([
            "web: Building 0.5s\n",
            "#1 [web 1/2] FROM node:18-alpine\n",
        ])
        process.wait.return_value = 0
        mock_popen.return_value = process

        log_file = io.StringIO()
        returncode = run_streamed(["docker", "compose", "build"], log_file, echo=False, group_by_image=True)

        self.assertEqual(returncode, 0)
        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list) if mock_stdout.write.call_args_list else ""
        self.assertNotIn("── Building", written)
        self.assertIn("web: Building 0.5s\n", log_file.getvalue())


class LogTailTest(unittest.TestCase):
    @patch("cli.up_runner.subprocess.Popen")
    def test_start_log_tail_pipes_logs_to_file_not_terminal(self, mock_popen):
        log_file = io.StringIO()
        start_log_tail("docker-compose.yml", log_file)

        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[:4], ["docker", "compose", "-f", "docker-compose.yml"])
        self.assertIn("logs", cmd)
        self.assertIn("-f", cmd)
        self.assertEqual(mock_popen.call_args.kwargs["stdout"], log_file)

    def test_stop_log_tail_terminates_running_process(self):
        process = MagicMock()
        process.poll.return_value = None

        stop_log_tail(process)

        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_stop_log_tail_is_a_noop_for_already_exited_process(self):
        process = MagicMock()
        process.poll.return_value = 0

        stop_log_tail(process)

        process.terminate.assert_not_called()

    def test_stop_log_tail_kills_process_that_wont_terminate(self):
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=5), None]

        stop_log_tail(process)

        process.kill.assert_called_once()


class DefaultRedrawTest(unittest.TestCase):
    @patch("cli.up_runner.sys.stdout")
    def test_emits_ansi_clear_codes_on_a_real_tty(self, mock_stdout):
        mock_stdout.isatty.return_value = True

        from cli.up_runner import _default_redraw

        _default_redraw("state text")

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("\033[2J\033[H", written)
        self.assertIn("state text", written)

    @patch("cli.up_runner.sys.stdout")
    def test_does_not_emit_ansi_codes_on_a_non_tty(self, mock_stdout):
        mock_stdout.isatty.return_value = False

        from cli.up_runner import _default_redraw

        _default_redraw("state text")

        written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertNotIn("\033[2J\033[H", written)
        self.assertIn("state text", written)


class PollStateUntilSettledTest(unittest.TestCase):
    def test_settles_immediately_when_all_services_already_running(self):
        ps_fn = MagicMock(return_value=_ps('{"Service": "web", "State": "running"}'))
        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            now_fn=MagicMock(side_effect=[0.0, 0.0]),
            redraw_fn=MagicMock(),
        )
        self.assertTrue(settled)
        self.assertEqual(grouped, {"RUNNING": ["web"]})
        ps_fn.assert_called_once()

    def test_polls_again_while_a_service_is_still_starting(self):
        starting = _ps('{"Service": "web", "Health": "starting"}')
        healthy = _ps('{"Service": "web", "Health": "healthy"}')
        ps_fn = MagicMock(side_effect=[starting, healthy])
        sleep_fn = MagicMock()

        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            ps_fn=ps_fn,
            sleep_fn=sleep_fn,
            now_fn=MagicMock(side_effect=[0.0, 0.0, 1.0]),
            redraw_fn=MagicMock(),
        )

        self.assertTrue(settled)
        self.assertEqual(grouped, {"HEALTHY": ["web"]})
        self.assertEqual(ps_fn.call_count, 2)
        sleep_fn.assert_called_once()

    def test_waits_for_all_expected_services_before_declaring_settled(self):
        one_service = _ps('{"Service": "web", "Health": "healthy"}')
        both_services = _ps(
            '{"Service": "web", "Health": "healthy"}\n{"Service": "db", "State": "running"}'
        )
        ps_fn = MagicMock(side_effect=[one_service, both_services])

        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            expected_service_count=2,
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            now_fn=MagicMock(side_effect=[0.0, 0.0, 1.0]),
            redraw_fn=MagicMock(),
        )

        self.assertTrue(settled)
        self.assertEqual(ps_fn.call_count, 2)
        self.assertIn("db", grouped.get("RUNNING", []))

    def test_returns_not_settled_when_a_service_is_unhealthy(self):
        ps_fn = MagicMock(return_value=_ps('{"Service": "web", "Health": "unhealthy"}'))
        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            now_fn=MagicMock(side_effect=[0.0, 0.0]),
            redraw_fn=MagicMock(),
        )
        self.assertFalse(settled)
        self.assertEqual(grouped, {"UNHEALTHY": ["web"]})

    def test_times_out_if_a_service_never_leaves_starting(self):
        stuck = _ps('{"Service": "web", "Health": "starting"}')
        ps_fn = MagicMock(return_value=stuck)
        sleep_fn = MagicMock()

        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            timeout=10,
            ps_fn=ps_fn,
            sleep_fn=sleep_fn,
            now_fn=MagicMock(side_effect=[0.0, 0.0, 100.0]),
            redraw_fn=MagicMock(),
        )

        self.assertFalse(settled)
        self.assertEqual(grouped, {"STARTING": ["web"]})
        sleep_fn.assert_called_once()

    def test_redraws_on_every_poll(self):
        ps_fn = MagicMock(return_value=_ps('{"Service": "web", "State": "running"}'))
        redraw_fn = MagicMock()

        poll_state_until_settled(
            "docker-compose.yml",
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            now_fn=MagicMock(side_effect=[0.0, 0.0]),
            redraw_fn=redraw_fn,
        )

        redraw_fn.assert_called_once()
        self.assertIn("RUNNING", redraw_fn.call_args[0][0])

    def test_treats_failed_ps_call_as_no_services_yet(self):
        failed = _ps("", returncode=1)
        healthy = _ps('{"Service": "web", "State": "running"}')
        ps_fn = MagicMock(side_effect=[failed, healthy])

        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            now_fn=MagicMock(side_effect=[0.0, 0.0, 1.0]),
            redraw_fn=MagicMock(),
        )

        self.assertTrue(settled)
        self.assertEqual(ps_fn.call_count, 2)

    def test_bails_out_immediately_when_up_fails_instead_of_waiting_out_timeout(self):
        # Services never came up because `docker compose up` itself failed;
        # this must return right away rather than polling until `timeout`.
        stuck = _ps('{"Service": "web", "Health": "starting"}')
        ps_fn = MagicMock(return_value=stuck)
        sleep_fn = MagicMock()
        up_done_fn = MagicMock(return_value=17)

        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            timeout=180,
            ps_fn=ps_fn,
            sleep_fn=sleep_fn,
            now_fn=MagicMock(return_value=0.0),
            redraw_fn=MagicMock(),
            up_done_fn=up_done_fn,
        )

        self.assertFalse(settled)
        self.assertEqual(grouped, {"STARTING": ["web"]})
        sleep_fn.assert_not_called()
        ps_fn.assert_called_once()

    def test_settle_timeout_only_starts_once_up_finishes_successfully(self):
        # `up` is still running (up_done_fn returns None) for the first two
        # polls; the clock must not start until it reports success, so a
        # stack whose healthchecks legitimately outlast `timeout` isn't
        # penalized for time `up` itself spent blocked beforehand.
        stuck = _ps('{"Service": "web", "Health": "starting"}')
        ps_fn = MagicMock(return_value=stuck)
        up_done_fn = MagicMock(side_effect=[None, None, 0, 0])

        settled, grouped = poll_state_until_settled(
            "docker-compose.yml",
            timeout=10,
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            # Only consumed once `up_done_fn` reports success (3rd/4th
            # poll): one call to start the clock, one to observe it hasn't
            # expired yet, one to observe it has. The two `None` polls
            # before that must not consume a `now_fn()` call at all.
            now_fn=MagicMock(side_effect=[0.0, 0.0, 100.0]),
            redraw_fn=MagicMock(),
            up_done_fn=up_done_fn,
        )

        self.assertFalse(settled)
        self.assertEqual(grouped, {"STARTING": ["web"]})

    def test_on_up_finished_callback_fires_once_when_up_completes(self):
        starting = _ps('{"Service": "web", "Health": "starting"}')
        healthy = _ps('{"Service": "web", "Health": "healthy"}')
        ps_fn = MagicMock(side_effect=[starting, healthy])
        on_up_finished = MagicMock()

        settled, _grouped = poll_state_until_settled(
            "docker-compose.yml",
            ps_fn=ps_fn,
            sleep_fn=MagicMock(),
            now_fn=MagicMock(side_effect=[0.0, 0.0, 1.0]),
            redraw_fn=MagicMock(),
            up_done_fn=MagicMock(return_value=0),
            on_up_finished=on_up_finished,
        )

        self.assertTrue(settled)
        on_up_finished.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
