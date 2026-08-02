import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.image_updates import (
    _read_max_pages,
    check_image_update,
    collect_module_images,
    extract_base_image,
    fetch_dockerhub_tags,
    find_images_in_compose,
    find_newer_tag,
    is_docker_hub_image,
    is_local_image,
    normalize_semver,
    parse_image_reference,
    semver_key,
)


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ParseImageReferenceRegressionTest(unittest.TestCase):
    def test_registry_with_port_and_single_repository_segment(self):
        parsed = parse_image_reference("localhost:5000/app")

        self.assertEqual(
            parsed,
            {
                "registry": "localhost:5000",
                "namespace": None,
                "repository": "app",
                "tag": "latest",
            },
        )

    def test_registry_with_port_single_repository_segment_and_tag(self):
        parsed = parse_image_reference("localhost:5000/app:1.2.3")

        self.assertEqual(
            parsed,
            {
                "registry": "localhost:5000",
                "namespace": None,
                "repository": "app",
                "tag": "1.2.3",
            },
        )


class FetchDockerhubTagsRegressionTest(unittest.TestCase):
    @patch("cli.image_updates.urlopen")
    def test_non_http_next_url_stops_pagination_instead_of_following_it(self, mock_urlopen):
        first_page = {
            "results": [{"name": "1.0"}],
            "next": "file:///etc/passwd",
        }
        mock_urlopen.return_value = _FakeResponse(first_page)

        tags = fetch_dockerhub_tags("library", "python")

        self.assertEqual(tags, ["1.0"])
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("cli.image_updates.urlopen")
    def test_http_next_url_is_still_followed_for_normal_pagination(self, mock_urlopen):
        first_page = {
            "results": [{"name": "1.0"}],
            "next": "https://hub.docker.com/v2/repositories/library/python/tags?page=2",
        }
        second_page = {
            "results": [{"name": "1.1"}],
            "next": None,
        }
        mock_urlopen.side_effect = [_FakeResponse(first_page), _FakeResponse(second_page)]

        tags = fetch_dockerhub_tags("library", "python")

        self.assertEqual(tags, ["1.0", "1.1"])
        self.assertEqual(mock_urlopen.call_count, 2)


class ReadMaxPagesTest(unittest.TestCase):
    def test_default_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("CDS_DOCKERHUB_MAX_PAGES", None)
            self.assertEqual(_read_max_pages(), 3)

    def test_invalid_value_falls_back_to_default(self):
        with patch.dict("os.environ", {"CDS_DOCKERHUB_MAX_PAGES": "not-a-number"}):
            self.assertEqual(_read_max_pages(), 3)

    def test_non_positive_value_falls_back_to_default(self):
        with patch.dict("os.environ", {"CDS_DOCKERHUB_MAX_PAGES": "0"}):
            self.assertEqual(_read_max_pages(), 3)
        with patch.dict("os.environ", {"CDS_DOCKERHUB_MAX_PAGES": "-5"}):
            self.assertEqual(_read_max_pages(), 3)

    def test_valid_positive_value_is_used(self):
        with patch.dict("os.environ", {"CDS_DOCKERHUB_MAX_PAGES": "7"}):
            self.assertEqual(_read_max_pages(), 7)


class ParseImageReferenceTest(unittest.TestCase):
    def test_bare_name_defaults_to_docker_hub_library(self):
        info = parse_image_reference("postgres")
        self.assertEqual(
            info,
            {"registry": "docker.io", "namespace": "library", "repository": "postgres", "tag": "latest"},
        )

    def test_namespace_and_repository(self):
        info = parse_image_reference("bitnami/postgresql")
        self.assertEqual(info["registry"], "docker.io")
        self.assertEqual(info["namespace"], "bitnami")
        self.assertEqual(info["repository"], "postgresql")
        self.assertEqual(info["tag"], "latest")

    def test_explicit_tag(self):
        info = parse_image_reference("postgres:16.2")
        self.assertEqual(info["repository"], "postgres")
        self.assertEqual(info["tag"], "16.2")

    def test_digest_is_stripped_before_tag_parsing(self):
        info = parse_image_reference("postgres@sha256:" + "a" * 64)
        self.assertEqual(info["repository"], "postgres")
        self.assertEqual(info["tag"], "latest")

    def test_custom_registry_with_namespace_and_repository(self):
        info = parse_image_reference("ghcr.io/acme/app:1.0")
        self.assertEqual(info["registry"], "ghcr.io")
        self.assertEqual(info["namespace"], "acme")
        self.assertEqual(info["repository"], "app")
        self.assertEqual(info["tag"], "1.0")


class RegistryClassificationTest(unittest.TestCase):
    def test_is_docker_hub_image_true_for_bare_and_namespaced(self):
        self.assertTrue(is_docker_hub_image("postgres"))
        self.assertTrue(is_docker_hub_image("bitnami/postgresql"))
        self.assertTrue(is_docker_hub_image("registry-1.docker.io/library/postgres"))

    def test_is_docker_hub_image_false_for_other_registries(self):
        self.assertFalse(is_docker_hub_image("ghcr.io/acme/app"))

    def test_is_local_image_true_for_custom_tag(self):
        self.assertTrue(is_local_image("myapp:custom"))

    def test_is_local_image_false_for_non_docker_hub_registry(self):
        self.assertFalse(is_local_image("ghcr.io/acme/app"))

    def test_is_local_image_false_for_docker_hub_image(self):
        self.assertFalse(is_local_image("postgres:16"))


class SemverTest(unittest.TestCase):
    def test_normalize_full_semver(self):
        self.assertEqual(normalize_semver("1.2.3"), "1.2.3")

    def test_normalize_partial_semver_fills_zeros(self):
        self.assertEqual(normalize_semver("16"), "16.0.0")
        self.assertEqual(normalize_semver("16.2"), "16.2.0")

    def test_normalize_strips_prerelease_and_build_metadata(self):
        self.assertEqual(normalize_semver("1.2.3-alpine"), "1.2.3")
        self.assertEqual(normalize_semver("1.2.3+build5"), "1.2.3")

    def test_normalize_non_semver_returns_none(self):
        self.assertIsNone(normalize_semver("latest"))
        self.assertIsNone(normalize_semver("bookworm"))

    def test_semver_key_orders_correctly(self):
        self.assertEqual(semver_key("1.2.3"), (1, 2, 3))
        self.assertIsNone(semver_key("latest"))


class FindNewerTagTest(unittest.TestCase):
    def test_finds_newer_patch_within_same_minor(self):
        result = find_newer_tag("1.2.3", ["1.2.3", "1.2.4", "1.3.0", "2.0.0"])
        self.assertEqual(result, "1.2.4")

    def test_no_update_when_already_latest(self):
        result = find_newer_tag("1.2.4", ["1.2.3", "1.2.4"])
        self.assertIsNone(result)

    def test_current_tag_not_semver_returns_none(self):
        self.assertIsNone(find_newer_tag("latest", ["1.2.3"]))

    def test_major_only_current_matches_any_minor_patch(self):
        result = find_newer_tag("16", ["16.1", "16.2", "17.0"])
        self.assertEqual(result, "16.2")

    def test_ignores_non_semver_candidate_tags(self):
        result = find_newer_tag("1.2.3", ["1.2.4", "latest", "bookworm"])
        self.assertEqual(result, "1.2.4")


class ExtractBaseImageTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dockerfile = Path(self._tmpdir.name) / "Dockerfile"

    def _write(self, content: str) -> Path:
        self.dockerfile.write_text(content, encoding="utf-8")
        return self.dockerfile

    def test_missing_file_returns_none(self):
        missing = Path(self._tmpdir.name) / "does-not-exist"
        self.assertIsNone(extract_base_image(missing))

    def test_single_stage_returns_image(self):
        self._write("FROM python:3.14-slim\nRUN pip install .\n")
        self.assertEqual(extract_base_image(self.dockerfile), "python:3.14-slim")

    def test_multi_stage_default_returns_final_stage(self):
        self._write(
            "FROM golang:1.22 AS builder\nRUN go build\nFROM alpine:3.19\nCOPY --from=builder /app /app\n"
        )
        self.assertEqual(extract_base_image(self.dockerfile), "alpine:3.19")

    def test_multi_stage_first_stage_when_final_stage_false(self):
        self._write("FROM golang:1.22 AS builder\nFROM alpine:3.19\n")
        self.assertEqual(extract_base_image(self.dockerfile, final_stage=False), "golang:1.22")

    def test_scratch_is_skipped(self):
        self._write("FROM scratch\nCOPY app /app\n")
        self.assertIsNone(extract_base_image(self.dockerfile))

    def test_arg_substituted_image_is_skipped(self):
        self._write("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n")
        self.assertIsNone(extract_base_image(self.dockerfile))

    def test_bare_from_with_no_image_is_ignored(self):
        self._write("FROM\nFROM alpine:3.19\n")
        self.assertEqual(extract_base_image(self.dockerfile), "alpine:3.19")

    def test_case_insensitive_from_keyword(self):
        self._write("from alpine:3.19\n")
        self.assertEqual(extract_base_image(self.dockerfile), "alpine:3.19")


class FindImagesInComposeTest(unittest.TestCase):
    def test_finds_image_at_root(self):
        result = find_images_in_compose({"image": "postgres:16"})
        self.assertEqual(result, [("<root>", "postgres:16", None)])

    def test_finds_images_across_services(self):
        compose = {
            "services": {
                "db": {"image": "postgres:16"},
                "web": {"image": "nginx:1.27"},
            }
        }
        result = find_images_in_compose(compose)
        self.assertEqual(
            sorted(result),
            sorted([("db", "postgres:16", None), ("web", "nginx:1.27", None)]),
        )

    def test_resolves_dockerfile_for_string_build_shorthand(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp)
            build_dir = module_dir / "docker"
            build_dir.mkdir()
            (build_dir / "Dockerfile").write_text("FROM alpine:3.19\n", encoding="utf-8")

            compose = {"services": {"app": {"image": "myapp:custom", "build": "docker"}}}
            result = find_images_in_compose(compose, module_dir=module_dir)

            self.assertEqual(len(result), 1)
            service_name, image, dockerfile = result[0]
            self.assertEqual(service_name, "app")
            self.assertEqual(image, "myapp:custom")
            self.assertEqual(dockerfile, build_dir / "Dockerfile")

    def test_resolves_dockerfile_for_dict_build_with_custom_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp)
            build_dir = module_dir / "docker"
            build_dir.mkdir()
            (build_dir / "Dockerfile.prod").write_text("FROM alpine:3.19\n", encoding="utf-8")

            compose = {
                "services": {
                    "app": {
                        "image": "myapp:custom",
                        "build": {"context": "docker", "dockerfile": "Dockerfile.prod"},
                    }
                }
            }
            result = find_images_in_compose(compose, module_dir=module_dir)

            self.assertEqual(result[0][2], build_dir / "Dockerfile.prod")

    def test_missing_dockerfile_yields_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_dir = Path(tmp)
            compose = {"services": {"app": {"image": "myapp:custom", "build": "docker"}}}
            result = find_images_in_compose(compose, module_dir=module_dir)
            self.assertIsNone(result[0][2])

    def test_does_not_recurse_into_build_block(self):
        compose = {"services": {"app": {"image": "myapp:custom", "build": {"context": "."}}}}
        result = find_images_in_compose(compose)
        self.assertEqual(len(result), 1)

    def test_handles_list_input(self):
        result = find_images_in_compose([{"image": "postgres:16"}, {"image": "nginx:1.27"}])
        self.assertEqual(
            sorted(result),
            sorted([("<root>", "postgres:16", None), ("<root>", "nginx:1.27", None)]),
        )

    def test_non_dict_non_list_returns_empty(self):
        self.assertEqual(find_images_in_compose("not-a-compose-doc"), [])


class CollectModuleImagesTest(unittest.TestCase):
    def test_collects_images_from_module_yaml_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_root = Path(tmp)
            module_dir = module_root / "warehouse" / "postgres"
            module_dir.mkdir(parents=True)
            (module_dir / "module.yaml").write_text(
                """
apiVersion: cds/v1alpha1
kind: Module
spec:
  implementation:
    kind: docker-compose
    compose:
      services:
        db:
          image: postgres:16
""",
                encoding="utf-8",
            )

            images = collect_module_images(module_root)

            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]["module"], str(Path("warehouse") / "postgres"))
            self.assertEqual(images[0]["service"], "db")
            self.assertEqual(images[0]["image"], "postgres:16")
            self.assertNotIn("dockerfile", images[0])

    def test_skips_unparseable_module_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            module_root = Path(tmp)
            module_dir = module_root / "broken"
            module_dir.mkdir()
            (module_dir / "module.yaml").write_text(": not valid yaml :::", encoding="utf-8")

            self.assertEqual(collect_module_images(module_root), [])


class CheckImageUpdateTest(unittest.TestCase):
    def test_local_custom_tag_without_dockerfile(self):
        result = check_image_update("myapp:custom")
        self.assertEqual(result, {"image": "myapp:custom", "status": "local", "latest": None})

    def test_local_image_with_dockerfile_recurses_on_base_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM postgres:16\n", encoding="utf-8")

            with patch("cli.image_updates.fetch_dockerhub_tags", return_value=["16", "16.1"]):
                result = check_image_update("myapp:custom", dockerfile=dockerfile)

            self.assertEqual(result["image"], "myapp:custom")
            self.assertEqual(result["base_image"], "postgres:16")
            self.assertEqual(result["status"], "update-available")
            self.assertEqual(result["latest"], "16.1")

    def test_local_image_dockerfile_with_no_resolvable_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")

            result = check_image_update("myapp:custom", dockerfile=dockerfile)

            self.assertEqual(result, {"image": "myapp:custom", "status": "local-no-base", "latest": None})

    def test_build_image_with_third_party_registry_recurses_on_base_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM postgres:16\n", encoding="utf-8")

            with patch("cli.image_updates.fetch_dockerhub_tags", return_value=["16"]):
                result = check_image_update("ghcr.io/acme/app:1.0", dockerfile=dockerfile)

            self.assertEqual(result["image"], "ghcr.io/acme/app:1.0")
            self.assertEqual(result["base_image"], "postgres:16")
            self.assertEqual(result["status"], "up-to-date")

    def test_non_docker_hub_registry_is_unsupported(self):
        result = check_image_update("ghcr.io/acme/app:1.0")
        self.assertEqual(
            result,
            {"image": "ghcr.io/acme/app:1.0", "status": "unsupported-registry", "latest": None},
        )

    def test_registry_one_docker_io_uses_docker_hub_lookup(self):
        with patch("cli.image_updates.fetch_dockerhub_tags", return_value=["16"]):
            result = check_image_update("registry-1.docker.io/library/postgres:16")

        self.assertEqual(result["status"], "up-to-date")

    def test_lookup_failed_when_fetch_returns_none(self):
        with patch("cli.image_updates.fetch_dockerhub_tags", return_value=None):
            result = check_image_update("postgres:16")
        self.assertEqual(result["status"], "lookup-failed")

    def test_update_available(self):
        with patch("cli.image_updates.fetch_dockerhub_tags", return_value=["16", "16.1", "16.2"]):
            result = check_image_update("postgres:16")
        self.assertEqual(result["status"], "update-available")
        self.assertEqual(result["latest"], "16.2")

    def test_up_to_date(self):
        with patch("cli.image_updates.fetch_dockerhub_tags", return_value=["16.2"]):
            result = check_image_update("postgres:16.2")
        self.assertEqual(result["status"], "up-to-date")
        self.assertIsNone(result["latest"])


if __name__ == "__main__":
    unittest.main()
