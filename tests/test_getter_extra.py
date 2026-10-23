import contextlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from cli import getter
from cli.getter import (
    CopyAction,
    GetError,
    _add_copy_action,
    _asset_root_relative_path,
    _check_source_allowed,
    _collect_asset_roots,
    _collect_build_assets,
    _collect_extends_profile_dirs,
    _collect_module_runtime_assets,
    _download_github_repository,
    _expand_abbreviated_commit,
    _extract_dockerfile_instruction_sources,
    _find_conflicts,
    _module_interpolation_context,
    _parse_dockerfile_assets,
    _prepare_source,
    _read_tracking_manifest,
    _require_within_repo,
    _resolve_existing_local_path,
    _resolve_source_profile_path,
    _strip_dockerfile_instruction_options,
    _validate_commit_pin,
    _validate_source_repository,
    _write_actions,
    format_get_plan,
)

FULL_SHA = "a" * 40


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _tar_bytes(members: dict[str, bytes], *, comment: str | None = None) -> bytes:
    buffer = io.BytesIO()
    pax = {"comment": comment} if comment is not None else None
    with tarfile.open(
        fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers=pax
    ) as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _fake_response(data: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = data
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class DownloadErrorTest(unittest.TestCase):
    def _download(self, urlopen_mock):
        with tempfile.TemporaryDirectory() as tmp, patch.object(getter, "urlopen", urlopen_mock):
            return _download_github_repository("o", "r", "main", Path(tmp))

    def test_http_error_is_wrapped(self) -> None:
        err = HTTPError("u", 404, "nf", {}, io.BytesIO())
        self.addCleanup(err.close)
        with self.assertRaisesRegex(GetError, "HTTP 404"):
            self._download(MagicMock(side_effect=err))

    def test_url_error_is_wrapped(self) -> None:
        with self.assertRaisesRegex(GetError, "boom"):
            self._download(MagicMock(side_effect=URLError("boom")))

    def test_bad_archive_is_wrapped(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not extract archive"):
            self._download(MagicMock(return_value=_fake_response(b"not a tarball")))

    def test_path_traversal_member_is_rejected(self) -> None:
        data = _tar_bytes({"../evil.txt": b"x"})
        with self.assertRaisesRegex(GetError, "Could not extract archive"):
            self._download(MagicMock(return_value=_fake_response(data)))

    def test_absolute_member_does_not_escape(self) -> None:
        data = _tar_bytes({"/abs-evil/x.txt": b"x"})
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            getter, "urlopen", MagicMock(return_value=_fake_response(data))
        ):
            try:
                _download_github_repository("o", "r", "main", Path(tmp))
            except GetError:
                pass
            self.assertFalse(Path("/abs-evil").exists())

    def test_multiple_top_level_dirs_rejected(self) -> None:
        data = _tar_bytes({"a/x": b"1", "b/y": b"2"})
        with self.assertRaisesRegex(GetError, "Unexpected archive layout"):
            self._download(MagicMock(return_value=_fake_response(data)))

    def test_top_level_file_only_rejected(self) -> None:
        data = _tar_bytes({"x": b"1"})
        with self.assertRaisesRegex(GetError, "Unexpected archive layout"):
            self._download(MagicMock(return_value=_fake_response(data)))

    def test_commit_from_pax_comment(self) -> None:
        data = _tar_bytes({"o-r-abcdef1/x": b"1"}, comment=FULL_SHA)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            getter, "urlopen", MagicMock(return_value=_fake_response(data))
        ):
            root, commit = _download_github_repository("o", "r", "main", Path(tmp))
            self.assertEqual(commit, FULL_SHA)
            self.assertTrue(root.is_dir())

    def test_commit_from_directory_suffix(self) -> None:
        data = _tar_bytes({"o-r-abcdef1/x": b"1"})
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            getter, "urlopen", MagicMock(return_value=_fake_response(data))
        ):
            _root, commit = _download_github_repository("o", "r", "main", Path(tmp))
            self.assertEqual(commit, "abcdef1")

    def test_no_commit_when_suffix_not_sha(self) -> None:
        data = _tar_bytes({"project/x": b"1"})
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            getter, "urlopen", MagicMock(return_value=_fake_response(data))
        ):
            _root, commit = _download_github_repository("o", "r", "main", Path(tmp))
            self.assertIsNone(commit)


class ExpandAbbreviatedCommitTest(unittest.TestCase):
    def test_success(self) -> None:
        resp = _fake_response((FULL_SHA + "\n").encode())
        with patch.object(getter, "urlopen", return_value=resp):
            self.assertEqual(_expand_abbreviated_commit("o", "r", "main", "aaaaaaa"), FULL_SHA)

    def test_network_failure(self) -> None:
        with patch.object(getter, "urlopen", side_effect=URLError("down")):
            with self.assertRaisesRegex(GetError, "could not resolve the full SHA"):
                _expand_abbreviated_commit("o", "r", "main", "aaaaaaa")

    def test_undecodable_response(self) -> None:
        with patch.object(getter, "urlopen", return_value=_fake_response(b"\xff\xfe")):
            with self.assertRaisesRegex(GetError, "could not resolve the full SHA"):
                _expand_abbreviated_commit("o", "r", "main", "aaaaaaa")

    def test_mismatching_sha(self) -> None:
        resp = _fake_response(("b" * 40).encode())
        with patch.object(getter, "urlopen", return_value=resp):
            with self.assertRaisesRegex(GetError, "does not match the downloaded archive"):
                _expand_abbreviated_commit("o", "r", "main", "aaaaaaa")

    def test_non_sha_response(self) -> None:
        with patch.object(getter, "urlopen", return_value=_fake_response(b"nope")):
            with self.assertRaisesRegex(GetError, "does not match the downloaded archive"):
                _expand_abbreviated_commit("o", "r", "main", "aaaaaaa")


class PrepareSourceTest(unittest.TestCase):
    def _run(self, *args, **kwargs):
        with getter._prepare_source(*args, **kwargs) as result:
            return result

    def test_local_with_remote_rejected(self) -> None:
        with self.assertRaisesRegex(GetError, "only one of --remote and --local"):
            self._run("o/r", getter.DEFAULT_REF, "/x")

    def test_local_with_ref_rejected(self) -> None:
        with self.assertRaisesRegex(GetError, "ignored with --local"):
            self._run(None, "dev", "/x")

    def test_local_with_commit_rejected(self) -> None:
        with self.assertRaisesRegex(GetError, "applies only to GitHub"):
            self._run(None, getter.DEFAULT_REF, "/x", commit="a" * 12)

    def test_invalid_commit_pin(self) -> None:
        with self.assertRaisesRegex(GetError, "Invalid --commit"):
            _validate_commit_pin("xyz")

    def test_unparseable_remote(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not resolve remote"):
            self._run("not a remote", "main", None)

    def test_disallowed_source(self) -> None:
        with self.assertRaisesRegex(GetError, "allowlist"):
            self._run("o/r", "main", None, allowed_sources=["other"])

    def test_allowlist_wildcard_and_repo(self) -> None:
        for allowed in (["*"], ["O/R"], ["o"], None, []):
            with self.subTest(allowed=allowed):
                self.assertIsNone(_check_source_allowed("o", "r", allowed))
        for allowed in (["other"], ["o/other"], ["o2/r"]):
            with self.subTest(allowed=allowed), self.assertRaisesRegex(
                GetError, "not in the trusted-source allowlist"
            ):
                _check_source_allowed("o", "r", allowed)

    def _with_download(self, resolved, pin, expanded=None):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "profiles").mkdir(parents=True)
            patches = [
                patch.object(getter, "_download_github_repository", return_value=(repo, resolved)),
            ]
            if expanded is not None:
                patches.append(patch.object(getter, "_expand_abbreviated_commit", return_value=expanded))
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                with _prepare_source("o/r", "main", None, commit=pin) as result:
                    return result

    def test_pin_without_resolved_commit(self) -> None:
        with self.assertRaisesRegex(GetError, "could not be verified"):
            self._with_download(None, "a" * 12)

    def test_pin_mismatch(self) -> None:
        with self.assertRaisesRegex(GetError, "does not match the pinned"):
            self._with_download("b" * 40, "a" * 12)

    def test_pin_expands_abbreviated(self) -> None:
        _path, commit = self._with_download("aaaaaaa", "a" * 12, expanded=FULL_SHA)
        self.assertEqual(commit, FULL_SHA)


class ValidateSourceRepositoryTest(unittest.TestCase):
    def test_missing_not_dir_and_no_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(GetError, "does not exist"):
                _validate_source_repository(root / "nope")
            _write(root / "file", "x")
            with self.assertRaisesRegex(GetError, "must be a directory"):
                _validate_source_repository(root / "file")
            (root / "empty").mkdir()
            with self.assertRaisesRegex(GetError, "profiles/ directory"):
                _validate_source_repository(root / "empty")


class ProfileResolutionTest(unittest.TestCase):
    def test_unresolvable_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(GetError, "Could not resolve profile"):
                _resolve_source_profile_path(Path(tmp), "missing")

    def test_flat_yaml_profile_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "profiles" / "flat.yaml", "kind: Profile\n")
            result = _resolve_source_profile_path(root, "flat")
            self.assertEqual(result, root / "profiles" / "flat.yaml")

    def test_profile_symlink_outside_repo_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp).resolve()
            _write(Path(outside) / "profile.yaml", "kind: Profile\n")
            (root / "profiles").mkdir()
            (root / "profiles" / "evil").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(GetError, "outside the source repository"):
                _resolve_source_profile_path(root, "evil")

    def test_require_within_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(GetError, "lbl resolves outside"):
                _require_within_repo(Path("/"), Path(tmp), "lbl")


class ExtendsAndAssetRootsTest(unittest.TestCase):
    def test_cycle_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "profiles" / "a" / "profile.yaml"
            _write(p, "kind: Profile\n")
            with self.assertRaisesRegex(GetError, "Cycle detected"):
                _collect_extends_profile_dirs(root, p, frozenset({p.resolve()}))

    def test_unloadable_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "profiles" / "a" / "profile.yaml"
            with patch.object(getter, "load_yaml_file", return_value=(None, [])):
                with self.assertRaisesRegex(GetError, "Could not load source profile"):
                    _collect_extends_profile_dirs(root, p)

    def test_no_extends_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "profiles" / "a" / "profile.yaml"
            with patch.object(getter, "load_yaml_file", return_value=({}, [])):
                self.assertEqual(_collect_extends_profile_dirs(root, p), set())

    def test_no_profiles_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "a" / "profile.yaml"
            with patch.object(getter, "load_yaml_file", return_value=({"extends": ["x"]}, [])), patch.object(
                getter, "_derive_profiles_root", return_value=None
            ):
                with self.assertRaisesRegex(GetError, "Could not derive a profiles root"):
                    _collect_extends_profile_dirs(root, p)

    def test_unresolvable_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "profiles" / "a" / "profile.yaml"
            with patch.object(getter, "load_yaml_file", return_value=({"extends": ["x"]}, [])), patch.object(
                getter, "_derive_profiles_root", return_value=root / "profiles"
            ), patch.object(getter, "_resolve_extends_ref", return_value=root / "missing.yaml"):
                with self.assertRaisesRegex(GetError, "Could not resolve extends parent"):
                    _collect_extends_profile_dirs(root, p)

    def test_parent_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp).resolve()
            parent = Path(outside) / "profile.yaml"
            _write(parent, "kind: Profile\n")
            p = root / "profiles" / "a" / "profile.yaml"
            with patch.object(getter, "load_yaml_file", return_value=({"extends": ["x"]}, [])), patch.object(
                getter, "_derive_profiles_root", return_value=root / "profiles"
            ), patch.object(getter, "_resolve_extends_ref", return_value=parent):
                with self.assertRaisesRegex(GetError, "outside the source repository"):
                    _collect_extends_profile_dirs(root, p)

    def _asset_roots(self, doc, diags=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "profiles" / "a" / "profile.yaml"
            _write(p, "kind: Profile\n")
            with patch.object(getter, "resolve_extends", return_value=(doc, {}, diags or [])), patch.object(
                getter, "_collect_extends_profile_dirs", return_value=set()
            ):
                return _collect_asset_roots(root, p)

    def test_profile_load_failure(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not load source profile"):
            self._asset_roots(None)

    def test_modules_not_list(self) -> None:
        with self.assertRaisesRegex(GetError, "must be a list"):
            self._asset_roots({"spec": {"modules": "x"}})

    def test_module_not_mapping(self) -> None:
        with self.assertRaisesRegex(GetError, "index 0 must be a mapping"):
            self._asset_roots({"spec": {"modules": ["x"]}})

    def test_module_missing_source(self) -> None:
        with self.assertRaisesRegex(GetError, "missing a source"):
            self._asset_roots({"spec": {"modules": [{"id": "m"}]}})

    def test_module_source_unresolvable(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not resolve module source"):
            self._asset_roots({"spec": {"modules": [{"id": "m", "source": "../../nope"}]}})


class ModuleAssetsTest(unittest.TestCase):
    def test_unloadable_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(GetError, "Could not load module"):
                _collect_module_runtime_assets(Path(tmp), Path(tmp), {})

    def _module_assets(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(getter, "load_yaml_file", return_value=(doc, [])):
                return _collect_module_runtime_assets(root, root, {})

    def test_compose_not_dict(self) -> None:
        self.assertEqual(
            self._module_assets({"spec": {"implementation": {"compose": []}}}), set()
        )

    def test_services_not_dict(self) -> None:
        doc = {"spec": {"implementation": {"compose": {"services": []}}}}
        self.assertEqual(self._module_assets(doc), set())

    def test_non_dict_service_skipped(self) -> None:
        doc = {"spec": {"implementation": {"compose": {"services": {"a": "x"}}}}}
        self.assertEqual(self._module_assets(doc), set())

    def test_interpolation_context_variants(self) -> None:
        self.assertIsNone(_module_interpolation_context({}, {}))
        schema = {"spec": {"configSchema": {"type": "object"}}}
        self.assertIsNone(_module_interpolation_context(schema, {"config": "str"}))
        self.assertIsNotNone(_module_interpolation_context(schema, {"config": None}))
        with patch.object(
            getter, "apply_defaults", side_effect=getter.MaxNestingDepthExceeded("deep")
        ):
            self.assertIsNone(_module_interpolation_context(schema, {}))


class BuildAssetsTest(unittest.TestCase):
    def _build(self, build, files=None, ctx=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, content in (files or {}).items():
                _write(root / name, content)
            return _collect_build_assets(
                root, root, build, interpolation_context=ctx
            ), root

    def test_none_and_unsupported(self) -> None:
        self.assertEqual(self._build(None)[0], set())
        self.assertEqual(self._build(5)[0], set())

    def test_unresolved_templates_and_empty(self) -> None:
        self.assertEqual(self._build({"context": ""})[0], set())
        self.assertEqual(self._build({"context": "${config.x}"})[0], set())
        self.assertEqual(self._build({"context": ".", "dockerfile": "${config.d}"})[0], set())
        self.assertEqual(self._build({"context": ".", "dockerfile": ""})[0], set())
        self.assertEqual(self._build({"context": ".", "dockerfile": 3})[0], set())

    def test_missing_context(self) -> None:
        self.assertEqual(self._build({"context": "nope"})[0], set())

    def test_string_build_without_dockerfile_returns_context(self) -> None:
        assets, root = self._build("sub", {"sub/a.txt": "x"})
        self.assertEqual(assets, {root / "sub"})

    def test_dockerfile_outside_repo_rejected(self) -> None:
        with self.assertRaisesRegex(GetError, "build.dockerfile"):
            self._build({"context": ".", "dockerfile": "../evil"})

    def test_context_outside_repo_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp).resolve()
            with self.assertRaisesRegex(GetError, "build context"):
                _collect_build_assets(
                    root, root, {"context": outside}, interpolation_context=None
                )

    def test_absolute_dockerfile_inside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "Dockerfile.x", "COPY a.txt /a\n")
            _write(root / "a.txt", "x")
            assets = _collect_build_assets(
                root,
                root,
                {"context": ".", "dockerfile": str(root / "Dockerfile.x")},
                interpolation_context=None,
            )
            self.assertIn(root / "Dockerfile.x", assets)
            self.assertIn(root / "a.txt", assets)

    def test_local_path_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "a", "x")
            self.assertEqual(_resolve_existing_local_path(str(root / "a"), []), root / "a")
            self.assertIsNone(_resolve_existing_local_path(str(root / "zz"), []))
            self.assertEqual(_resolve_existing_local_path("./a", [root]), root / "a")


class DockerfileParsingTest(unittest.TestCase):
    def test_unreadable_dockerfile(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not read Dockerfile"):
            _parse_dockerfile_assets(Path("/nonexistent"), Path("/nonexistent/Dockerfile"))

    def test_continuations_blank_lines_and_trailing_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "a.txt", "x")
            _write(root / "b.txt", "x")
            _write(
                root / "Dockerfile",
                "# comment\nFROM x\n\nCOPY a.txt \\\n  b.txt /dst/\n\nRUN echo\nADD a.txt \\\n",
            )
            assets = _parse_dockerfile_assets(root, root / "Dockerfile")
            self.assertEqual(assets, {root / "a.txt", root / "b.txt"})

    def test_options_stripping(self) -> None:
        self.assertEqual(_strip_dockerfile_instruction_options("--chown=1 a b"), ("a b", False))
        self.assertEqual(_strip_dockerfile_instruction_options("--from=x a b"), ("a b", True))
        self.assertEqual(_strip_dockerfile_instruction_options("--link"), ("", False))

    def _sources(self, line, files=("a.txt",)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for f in files:
                _write(root / f, "x")
            result = _extract_dockerfile_instruction_sources(root, root / "Dockerfile", line)
            return {p.relative_to(root).as_posix() if p != root else "." for p in result}

    def test_invalid_json_form(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not parse COPY"):
            self._sources('COPY ["a.txt", ')

    def test_json_form_variants(self) -> None:
        self.assertEqual(self._sources('COPY ["a.txt", "/dst"]'), {"a.txt"})
        self.assertEqual(self._sources('COPY ["a.txt"]'), set())
        self.assertEqual(self._sources("COPY [1, 2]"), set())

    def test_unbalanced_quotes(self) -> None:
        with self.assertRaisesRegex(GetError, "Could not parse ADD"):
            self._sources('ADD "a.txt /dst')

    def test_single_arg_and_from_stage_and_empty(self) -> None:
        self.assertEqual(self._sources("COPY a.txt"), set())
        self.assertEqual(self._sources("COPY --from=b a.txt /x"), set())
        self.assertEqual(self._sources("COPY --link"), set())

    def test_skipped_and_special_sources(self) -> None:
        line = "COPY . https://x/y http://x/y ${ARG} missing *.txt /dst"
        self.assertEqual(self._sources(line), {".", "a.txt"})

    def test_absolute_source_skipped(self) -> None:
        self.assertEqual(self._sources("COPY /abs a.txt /dst"), {"a.txt"})

    def test_missing_literal_source_ignored(self) -> None:
        self.assertEqual(self._sources("COPY nope /dst"), set())


class CopyAndManifestTest(unittest.TestCase):
    def test_add_copy_action_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            f = Path(outside) / "f"
            _write(f, "x")
            with self.assertRaisesRegex(GetError, "outside the source repository"):
                _add_copy_action(
                    source_repo=Path(tmp),
                    source_file=f,
                    destination_root=Path(tmp) / "dst",
                    actions_by_destination={},
                )

    def test_add_copy_action_duplicate_same_and_conflicting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "f", "x")
            actions: dict = {}
            kwargs = dict(source_repo=root, destination_root=root / "d", actions_by_destination=actions)
            _add_copy_action(source_file=root / "f", **kwargs)
            _add_copy_action(source_file=root / "f", **kwargs)
            self.assertEqual(len(actions), 1)
            key = next(iter(actions))
            _write(root / "g", "y")
            actions[key] = CopyAction(root / "g", key, "f")
            with self.assertRaisesRegex(GetError, "Multiple source files"):
                _add_copy_action(source_file=root / "f", **kwargs)

    def test_find_conflicts_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "src", "x")
            _write(root / "same", "x")
            _write(root / "diff", "y")
            (root / "dir").mkdir()
            (root / "link").symlink_to(root / "missing")
            actions = [
                CopyAction(root / "src", root / name, name)
                for name in ("same", "diff", "dir", "link", "absent")
            ]
            self.assertEqual(_find_conflicts(actions), ["diff", "dir", "link"])

    def test_write_actions_replaces_symlink_and_wraps_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _write(root / "src", "x")
            _write(root / "target", "orig")
            (root / "link").symlink_to(root / "target")
            _write_actions([CopyAction(root / "src", root / "link", "link")])
            self.assertFalse((root / "link").is_symlink())
            self.assertEqual((root / "target").read_text(), "orig")
            with patch.object(getter.shutil, "copy2", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(GetError, "Could not write"):
                    _write_actions([CopyAction(root / "src", root / "o", "o")])

    def test_read_manifest_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            _write(path, "{}")
            err = io.StringIO()
            with patch.object(Path, "read_text", side_effect=OSError("nope")), contextlib.redirect_stderr(err):
                self.assertEqual(_read_tracking_manifest(path), {"version": 1, "profiles": {}})
            self.assertIn("could not be read", err.getvalue())

    def test_read_manifest_malformed_variants(self) -> None:
        for content, reason in (("{bad", "invalid JSON"), ("[]", "must be a JSON object")):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "m.json"
                _write(path, content)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(_read_tracking_manifest(path), {"version": 1, "profiles": {}})
                self.assertIn(reason, err.getvalue())
                self.assertIn("backed up to", err.getvalue())

    def test_read_manifest_backup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            _write(path, "{bad")
            err = io.StringIO()
            with patch.object(getter.shutil, "copy2", side_effect=OSError("ro")), contextlib.redirect_stderr(err):
                _read_tracking_manifest(path)
            self.assertIn("backup failed", err.getvalue())

    def test_read_manifest_valid_gets_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            _write(path, json.dumps({"x": 1}))
            self.assertEqual(_read_tracking_manifest(path), {"x": 1, "version": 1, "profiles": {}})

    def test_write_manifest_resets_non_dict_profiles_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo = root / "repo"
            _write(repo / "profiles" / "p" / "profile.yaml", "x")
            target = root / "t"
            target.mkdir()
            manifest = target / ".cds" / "get-manifest.json"
            _write(manifest, json.dumps({"profiles": []}))
            outside = root / "outside.json"
            _write(outside, "orig")
            manifest.unlink()
            manifest.symlink_to(outside)
            with contextlib.redirect_stderr(io.StringIO()):
                getter._write_tracking_manifest(
                    target_root=target,
                    requested_profile="p",
                    source_repo=repo,
                    profile_path=repo / "profiles" / "p" / "profile.yaml",
                    remote=None,
                    ref="main",
                    local=None,
                    actions=[],
                    asset_roots=[],
                )
            self.assertFalse(manifest.is_symlink())
            self.assertEqual(outside.read_text(), "orig")
            self.assertIn("p", json.loads(manifest.read_text())["profiles"])

    def test_asset_root_relative_path_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            self.assertEqual(
                _asset_root_relative_path(Path(outside), Path(tmp)), Path(outside).resolve().as_posix()
            )


class FormatGetPlanTest(unittest.TestCase):
    def test_empty_and_truncated_lists(self) -> None:
        dest = Path("/dst")
        self.assertIn("No file changes", format_get_plan([], destination_root=dest))
        actions = [CopyAction(Path(f"/s/{i}"), dest / str(i), f"f{i:02d}") for i in range(45)]
        conflicts = [a.repo_relative_path for a in actions[:22]]
        text = format_get_plan(actions, destination_root=dest, conflicts=conflicts)
        self.assertIn("... 2 more", text)
        self.assertIn("... 3 more", text)
        self.assertIn("Would conflict without --force (22)", text)
