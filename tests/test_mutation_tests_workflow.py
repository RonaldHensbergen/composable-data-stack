import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "mutation-tests.yml"

_MUTMUT_AVAILABLE = importlib.util.find_spec("mutmut") is not None

# A complete mutants/<path>.meta file as written by mutmut 3.8.1's
# SourceFileMutationData.save() (exit code 0 == mutant survived).
_VALID_META = {
    "exit_code_by_key": {"foo.py::add-1": 0},
    "hash_by_function_name": {},
    "type_check_error_by_key": {},
    "durations_by_key": {"foo.py::add-1": 0.1},
    "estimated_durations_by_key": {"foo.py::add-1": 0.1},
}

# Emulation of the mutmut 3.8.1 subcommands the export step calls, reproducing
# the exact failure from issue #1175 when the cache is empty/partial:
# export-cicd-stats crashes with JSONDecodeError on an unparseable
# mutants/*.meta (mutmut/mutation/data.py load() only catches
# FileNotFoundError), and badge exits 2 when mutants/mutmut-cicd-stats.json is
# missing (click.Path(exists=True)). Used only when mutmut is not installed.
_MUTMUT_EMULATION = """import json
import sys
from pathlib import Path


def export_cicd_stats() -> int:
    data = {}
    for meta_path in sorted(Path("mutants").rglob("*.meta")):
        with open(meta_path) as f:
            meta = json.load(f)  # JSONDecodeError propagates, like mutmut
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

    def test_export_step_sanitizes_incomplete_meta_cache_before_export(self) -> None:
        run = self._step("Export stats, badge, and results")["run"]
        sanitize_at = run.find('rglob("*.meta")')
        export_at = run.find("python -m mutmut export-cicd-stats")
        self.assertGreater(
            sanitize_at, -1, "export step must scan mutants/*.meta cache files"
        )
        self.assertGreater(
            export_at,
            sanitize_at,
            "cache sanitization must run before export-cicd-stats",
        )
        # Incomplete cache files (empty/truncated on a time-capped run, #1175)
        # are dropped, not parsed: mutmut's load() only tolerates a missing file.
        self.assertIn("JSONDecodeError", run)
        self.assertIn("unlink", run)

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

    Reproduces issue #1175: before the fix, an empty mutants/*.meta (killed
    mid-write by the PR time cap) made `mutmut export-cicd-stats` crash with a
    JSONDecodeError and fail the job; with no results at all, `mutmut badge`
    exited 2 on the missing stats file. Uses real mutmut when installed,
    otherwise a stub that mirrors the same failure modes.
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

    def test_empty_meta_cache_no_longer_fails_the_export_step(self) -> None:
        # The original #1175 crash: time-capped run killed mid-write leaves an
        # empty mutants/foo.py.meta; export-cicd-stats used to die with
        # JSONDecodeError and the step exited 1.
        project = self._make_project({"foo.py.meta": ""})
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(self._stats(project)["total"], 0)
        self.assertTrue((project / "runner_temp" / "mutation-badge.svg").exists())

    def test_truncated_meta_cache_is_dropped(self) -> None:
        project = self._make_project(
            {"foo.py.meta": '{"exit_code_by_key": {"foo.py::add-1": 0}, "durations_by_'}
        )
        result = self._run_step(project)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse((project / "mutants" / "foo.py.meta").exists())
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
        self.assertFalse((project / "mutants" / "bar.py.meta").exists())
        self.assertTrue((project / "mutants" / "foo.py.meta").exists())


if __name__ == "__main__":
    unittest.main()
