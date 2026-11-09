import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "mutation-tests.yml"

_MUTMUT_AVAILABLE = importlib.util.find_spec("mutmut") is not None

# The torn mutants/*.meta tolerance (boxed/mutmut#630) exists only in this
# pinned git commit — installed ad hoc by `make test-mutation` and
# .github/workflows/mutation-tests.yml — or in a future PyPI release that
# ships it. mutmut is in no dependency extra, so a dev machine with a PyPI
# mutmut (3.8.1, which crashes with json.JSONDecodeError on an unparseable
# mutants/*.meta) installed must not turn an otherwise green suite red (#1240).
_MUTMUT_TORN_META_PIN = (
    "git+https://github.com/boxed/mutmut@6837b3c3ab0cfa4f5488f68adbd1bb26f68b8eb8"
)


def _installed_mutmut_version() -> str | None:
    if not _MUTMUT_AVAILABLE:
        return None
    try:
        return importlib.metadata.version("mutmut")
    except importlib.metadata.PackageNotFoundError:
        return None


def _installed_mutmut_tolerates_torn_meta() -> bool:
    """Probe the installed mutmut: does export-cicd-stats survive a torn mutants/*.meta?

    Runs the real CLI against an empty mutants/foo.py.meta. Returns False
    whenever the fix cannot be confirmed (probe error included), so the
    torn-cache tests skip instead of failing when an unpinned PyPI mutmut is
    installed (#1240).
    """
    if not _MUTMUT_AVAILABLE:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "mutants").mkdir()
        (project / "mutants" / "foo.py.meta").write_text("", encoding="utf-8")
        (project / "pyproject.toml").write_text(
            '[tool.mutmut]\nsource_paths = ["foo.py"]\n', encoding="utf-8"
        )
        (project / "foo.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-m", "mutmut", "export-cicd-stats"],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except OSError, subprocess.SubprocessError:
            return False
        return result.returncode == 0


# The export-step tests run against real mutmut only when it carries the
# boxed/mutmut#630 torn-.meta fix; a PyPI mutmut without it cannot run the
# torn-cache tests and must not fail the suite (#1240).
_MUTMUT_VERSION = _installed_mutmut_version()
_SKIP_WITHOUT_TORN_META_FIX = unittest.skipIf(
    _MUTMUT_AVAILABLE and not _installed_mutmut_tolerates_torn_meta(),
    "requires mutmut with the torn-.meta fix (boxed/mutmut#630): "
    f"pip install 'mutmut @ {_MUTMUT_TORN_META_PIN}' or uninstall mutmut; "
    f"the installed mutmut {_MUTMUT_VERSION or '(unknown version)'} lacks it (#1240)",
)

# A complete mutants/<path>.meta file as written by mutmut 3.8.1's
# SourceFileMutationData.save() (exit code 0 == mutant survived).
_VALID_META = {
    "exit_code_by_key": {"foo.py::add-1": 0},
    "hash_by_function_name": {},
    "type_check_error_by_key": {},
    "durations_by_key": {"foo.py::add-1": 0.1},
    "estimated_durations_by_key": {"foo.py::add-1": 0.1},
}

# Emulation of the mutmut subcommands the export step calls, mirroring the
# boxed/mutmut#630 behaviour (merged 2026-11-07; the workflow and Makefile pin
# that commit via git): SourceFileMutationData.load() catches
# json.JSONDecodeError alongside FileNotFoundError, so export-cicd-stats
# tolerates an unparseable mutants/*.meta instead of crashing with it
# (#1175/#1193). badge still exits 2 when mutants/mutmut-cicd-stats.json is
# missing (click.Path(exists=True)); the export step's placeholder guards that.
# Used only when mutmut is not installed.
_MUTMUT_EMULATION = """import json
import sys
from pathlib import Path


def export_cicd_stats() -> int:
    data = {}
    for meta_path in sorted(Path("mutants").rglob("*.meta")):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except json.JSONDecodeError:
            continue  # torn cache tolerated, like mutmut with boxed/mutmut#630
        if meta.get("exit_code_by_key"):
            data[str(meta_path)] = meta
    if not data:
        print('No previous mutation data found. Run "mutmut run" first.')
        return 0
    exits = [v for m in data.values() for v in m["exit_code_by_key"].values()]
    Path("mutants").mkdir(exist_ok=True)
    with open("mutants/mutmut-cicd-stats.json", "w") as f:
        json.dump(
            {
                "killed": sum(1 for v in exits if v != 0),
                "survived": sum(1 for v in exits if v == 0),
                "total": len(exits),
                "no_tests": 0,
                "skipped": 0,
                "suspicious": 0,
                "timeout": 0,
                "check_was_interrupted_by_user": 0,
                "segfault": 0,
            },
            f,
            indent=4,
        )
    print("Saved CI/CD stats to mutants/mutmut-cicd-stats.json")
    return 0


def badge(args: list) -> int:
    output = args[args.index("--output") + 1]
    try:
        with open("mutants/mutmut-cicd-stats.json") as f:
            stats = json.load(f)
    except FileNotFoundError:
        print(
            "Error: Invalid value for '--input': File 'mutants/mutmut-cicd-stats.json' does not exist.",
            file=sys.stderr,
        )
        return 2
    tested = stats["total"] - stats["skipped"]
    score = 0.0 if tested <= 0 else (stats["killed"] + stats["timeout"]) / tested * 100
    with open(output, "w") as f:
        json.dump({"schemaVersion": 1, "label": "mutation score", "message": f"{score:.1f}%"}, f)
    print(f"Saved mutation badge to {output}")
    return 0


def main() -> int:
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "export-cicd-stats":
        return export_cicd_stats()
    if cmd == "badge":
        return badge(rest)
    if cmd == "results":
        print("    foo.py::add-1: survived")
        return 0
    raise SystemExit(f"emulation: unsupported mutmut command {cmd}")


main()
"""


class MutationTestsWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.steps = self.workflow["jobs"]["kill-matrix"]["steps"]

    def _step(self, name: str) -> dict:
        step = next((s for s in self.steps if s.get("name") == name), None)
        self.assertIsNotNone(step, f"expected to find step {name!r}")
        return step

    def test_pr_runs_are_time_capped_and_partial_exit_124_is_accepted(self) -> None:
        run = self._step("Run kill matrix")["run"]
        self.assertIn("timeout 180 python -m mutmut run", run)
        # A time-capped matrix (exit 124) is a documented partial, not a failure.
        self.assertIn("$rc -ne 124", run)

    def test_mutmut_pin_carries_the_torn_meta_cache_fix(self) -> None:
        # The ad hoc pin (here and in the Makefile test-mutation target) must
        # not stay on mutmut 3.8.1, whose load() only catches FileNotFoundError
        # and crashes on a torn mutants/*.meta (#1175). It points at the
        # boxed/mutmut#630 commit until a release > 3.8.1 ships on PyPI, at
        # which point this pin flips to "mutmut==<release>" (#1193).
        run = self._step("Install dependencies")["run"]
        self.assertNotIn("mutmut==3.8.1", run)
        self.assertIn(
            "mutmut @ git+https://github.com/boxed/mutmut@6837b3c3ab0cfa4f5488f68adbd1bb26f68b8eb8",
            run,
        )
        makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn("mutmut==3.8.1", makefile)
        self.assertIn("boxed/mutmut@6837b3c3ab0cfa4f5488f68adbd1bb26f68b8eb8", makefile)

    def test_export_step_has_no_meta_cache_sanitizer(self) -> None:
        # boxed/mutmut#630 (merged) makes mutmut's load() treat an unparseable
        # mutants/*.meta like a missing one, so the export step must NOT
        # sanitize the cache itself anymore; it relies on mutmut (#1193).
        run = self._step("Export stats, badge, and results")["run"]
        export_at = run.find("python -m mutmut export-cicd-stats")
        self.assertGreater(export_at, -1, "export step must run export-cicd-stats")
        self.assertNotIn('rglob("*.meta")', run)
        self.assertNotIn("unlink", run)
        # No python heredoc either: the sanitizer block must not come back.
        self.assertNotIn("<<'EOF'", run)

    def test_export_step_writes_empty_stats_placeholder_before_badge(self) -> None:
        run = self._step("Export stats, badge, and results")["run"]
        placeholder_at = run.find("mutmut-cicd-stats.json")
        badge_at = run.find("python -m mutmut badge")
        self.assertGreater(placeholder_at, -1)
        self.assertGreater(
            badge_at, placeholder_at, "placeholder must be written before badge runs"
        )
        # The placeholder is an all-zero stats document so badge (which requires
        # an existing valid-JSON input) and the job summary still render.
        self.assertIn('"total": 0', run)

    def test_summary_and_artifact_steps_tolerate_missing_stats(self) -> None:
        summary_run = self._step("Mutation score summary")["run"]
        self.assertIn("_No stats exported._", summary_run)
        upload = self._step("Upload kill matrix report")
        self.assertEqual(upload["with"]["if-no-files-found"], "warn")


@unittest.skipIf(
    sys.platform == "win32",
    "the export step runs under bash on ubuntu-latest only; windows-latest has no bash",
)
class ExportStepFunctionalTest(unittest.TestCase):
    """Run the export step's script end-to-end against an empty/partial cache.

    Issue #1175/#1193: an empty mutants/*.meta (killed mid-write by the PR time
    cap) used to make `mutmut export-cicd-stats` crash with a JSONDecodeError
    and fail the job. With the boxed/mutmut#630 fix pinned in the workflow,
    mutmut itself now tolerates the torn file (load() catches
    json.JSONDecodeError), so the export step succeeds without any sanitizer
    and leaves the torn file in place. With no results at all, `mutmut badge`
    would still exit 2 on the missing stats file, which the placeholder
    guards. Uses real mutmut when installed, otherwise a stub that mirrors the
    fixed behaviour. When the installed mutmut lacks the #630 fix (e.g. PyPI
    3.8.1), the torn-cache tests skip instead of failing (#1240).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.bin_dir = base / "bin"
        self.bin_dir.mkdir()
        emu = base / "mutmut_emulation.py"
        emu.write_text(_MUTMUT_EMULATION, encoding="utf-8")
        stub = self.bin_dir / "python"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "${1:-}" == "-m" && "${2:-}" == "mutmut" && "${STUB_MUTMUT:-0}" == "1" ]]; then\n'
            f'  exec "{sys.executable}" "{emu}" "${{@:3}}"\n'
            "fi\n"
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        step = next(
            s
            for s in yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"][
                "kill-matrix"
            ]["steps"]
            if s.get("name") == "Export stats, badge, and results"
        )
        self.step_script = base / "step.sh"
        self.step_script.write_text(step["run"], encoding="utf-8")
        self._counter = 0

    def _make_project(self, meta_files: dict) -> Path:
        self._counter += 1
        project = Path(self._tmp.name) / f"proj{self._counter}"
        (project / "mutants").mkdir(parents=True)
        (project / "runner_temp").mkdir()
        (project / "pyproject.toml").write_text(
            '[tool.mutmut]\nsource_paths = ["foo.py"]\n', encoding="utf-8"
        )
        (project / "foo.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        for name, content in meta_files.items():
            meta_path = project / "mutants" / name
            meta_path.write_text(
                content, encoding="utf-8"
            )  # empty string == torn write
        return project

    def _run_step(self, project: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["RUNNER_TEMP"] = str(project / "runner_temp")
        # A time-capped run leaves no stats when no result was recorded, so the
        # stub stands in for mutmut only when mutmut itself is not installed.
        env["STUB_MUTMUT"] = "0" if _MUTMUT_AVAILABLE else "1"
        return subprocess.run(
            ["bash", "-e", str(self.step_script)],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _stats(self, project: Path) -> dict:
        return json.loads(
            (project / "mutants" / "mutmut-cicd-stats.json").read_text(encoding="utf-8")
        )

    @_SKIP_WITHOUT_TORN_META_FIX
    def test_empty_meta_cache_no_longer_fails_the_export_step(self) -> None:
        # The original #1175 crash: time-capped run killed mid-write leaves an
        # empty mutants/foo.py.meta; export-cicd-stats used to die with
        # JSONDecodeError and the step exited 1. mutmut now tolerates the torn
        # file itself (boxed/mutmut#630), so it is ignored, not deleted.
        project = self._make_project({"foo.py.meta": ""})
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(self._stats(project)["total"], 0)
        self.assertTrue((project / "mutants" / "foo.py.meta").exists())
        self.assertTrue((project / "runner_temp" / "mutation-badge.svg").exists())

    @_SKIP_WITHOUT_TORN_META_FIX
    def test_truncated_meta_cache_is_tolerated_not_dropped(self) -> None:
        # mutmut's load() treats an unparseable cache like a missing one
        # (boxed/mutmut#630), so the torn file is skipped, not unlinked.
        project = self._make_project(
            {"foo.py.meta": '{"exit_code_by_key": {"foo.py::add-1": 0}, "durations_by_'}
        )
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertTrue((project / "mutants" / "foo.py.meta").exists())
        self.assertEqual(self._stats(project)["total"], 0)

    def test_missing_cache_writes_empty_stats_placeholder(self) -> None:
        # Time-capped before any result: export-cicd-stats writes no stats file
        # and badge used to exit 2 on the missing input.
        project = self._make_project({})
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(self._stats(project), {k: 0 for k in self._stats(project)})
        self.assertEqual(self._stats(project)["total"], 0)
        self.assertTrue((project / "runner_temp" / "mutation-badge.svg").exists())

    def test_valid_results_are_exported_and_not_overwritten_by_placeholder(
        self,
    ) -> None:
        project = self._make_project({"foo.py.meta": json.dumps(_VALID_META)})
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        stats = self._stats(project)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["survived"], 1)
        self.assertTrue((project / "mutants" / "foo.py.meta").exists())

    def test_mixed_valid_and_empty_cache_keeps_valid_results(self) -> None:
        project = self._make_project(
            {"foo.py.meta": json.dumps(_VALID_META), "bar.py.meta": ""}
        )
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(self._stats(project)["total"], 1)
        # The empty cache is tolerated by mutmut (boxed/mutmut#630), not dropped.
        self.assertTrue((project / "mutants" / "bar.py.meta").exists())
        self.assertTrue((project / "mutants" / "foo.py.meta").exists())


class TornMetaProbeTest(unittest.TestCase):
    """Pin the #1240 probe decision logic with a mocked mutmut CLI.

    Hermetic: runs with or without mutmut installed, so CI (no mutmut) covers
    the skip-condition logic too.
    """

    def _probe(self, run_result=None, run_error=None) -> bool:
        module = sys.modules[__name__]
        with unittest.mock.patch.object(module, "_MUTMUT_AVAILABLE", True):
            with unittest.mock.patch.object(subprocess, "run") as run:
                if run_error is not None:
                    run.side_effect = run_error
                else:
                    run.return_value = run_result
                return _installed_mutmut_tolerates_torn_meta()

    def test_probe_passes_when_export_cicd_stats_succeeds(self) -> None:
        self.assertTrue(self._probe(run_result=subprocess.CompletedProcess([], 0)))

    def test_probe_fails_when_export_cicd_stats_crashes(self) -> None:
        self.assertFalse(self._probe(run_result=subprocess.CompletedProcess([], 1)))

    def test_probe_fails_on_subprocess_error(self) -> None:
        self.assertFalse(self._probe(run_error=subprocess.TimeoutExpired("mutmut", 60)))
        self.assertFalse(self._probe(run_error=OSError("boom")))

    def test_probe_never_runs_without_mutmut(self) -> None:
        module = sys.modules[__name__]
        with unittest.mock.patch.object(module, "_MUTMUT_AVAILABLE", False):
            with unittest.mock.patch.object(subprocess, "run") as run:
                self.assertFalse(_installed_mutmut_tolerates_torn_meta())
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
