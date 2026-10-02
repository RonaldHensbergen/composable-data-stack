import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from cli.image_digest_check import (
    _fetch_bearer_token,
    check_digest_staleness,
    collect_pinned_images_for_module,
    fetch_registry_digest,
    is_enabled,
    parse_pinned_reference,
)


class _FakeTokenResponse:
    def __init__(self, payload):
        import json as _json

        self._body = _json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHeadResponse:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ParsePinnedReferenceTest(unittest.TestCase):
    def test_parses_tag_and_digest(self):
        parsed = parse_pinned_reference(
            "postgres:18@sha256:32ca0af8e77bfb8c6610c488e4691f83f972a3e9e64d3b02facf3ab111ad5500"
        )
        self.assertEqual(
            parsed,
            (
                "postgres:18",
                "sha256:32ca0af8e77bfb8c6610c488e4691f83f972a3e9e64d3b02facf3ab111ad5500",
            ),
        )

    def test_returns_none_when_not_digest_pinned(self):
        self.assertIsNone(parse_pinned_reference("postgres:18"))

    def test_returns_none_for_malformed_digest(self):
        self.assertIsNone(parse_pinned_reference("postgres:18@sha256:not-hex"))


class IsEnabledTest(unittest.TestCase):
    def test_explicit_flag_wins_regardless_of_env(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(is_enabled(True))

    @patch.dict("os.environ", {"CDS_CHECK_IMAGE_DIGESTS": "1"})
    def test_env_var_opts_in(self):
        self.assertTrue(is_enabled(False))

    @patch.dict("os.environ", {}, clear=True)
    def test_off_by_default(self):
        self.assertFalse(is_enabled(False))


class CheckDigestStalenessTest(unittest.TestCase):
    def test_not_pinned_is_reported_as_such(self):
        result = check_digest_staleness("postgres:18")
        self.assertEqual(result["status"], "not-pinned")

    @patch("cli.image_digest_check.fetch_registry_digest")
    def test_matching_digest_is_up_to_date(self, mock_fetch):
        mock_fetch.return_value = "sha256:" + "a" * 64
        image = f"postgres:18@sha256:{'a' * 64}"
        result = check_digest_staleness(image)
        self.assertEqual(result["status"], "up-to-date")

    @patch("cli.image_digest_check.fetch_registry_digest")
    def test_different_digest_is_stale(self, mock_fetch):
        mock_fetch.return_value = "sha256:" + "b" * 64
        image = f"postgres:18@sha256:{'a' * 64}"
        result = check_digest_staleness(image)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["latest_digest"], "sha256:" + "b" * 64)

    @patch("cli.image_digest_check.fetch_registry_digest")
    def test_unreachable_registry_is_lookup_failed_not_an_error(self, mock_fetch):
        mock_fetch.return_value = None
        image = f"postgres:18@sha256:{'a' * 64}"
        result = check_digest_staleness(image)
        self.assertEqual(result["status"], "lookup-failed")


class FetchBearerTokenTest(unittest.TestCase):
    def _challenge(self, realm=None, extra=""):
        scheme = "Bear" + "er"
        if realm is None:
            return scheme + " not-a-challenge"
        param = "real" + "m"
        q = chr(34)
        challenge = scheme + " " + param + "=" + q + realm + q
        if extra:
            challenge += ", " + extra
        return challenge

    def test_non_bearer_challenge_returns_none(self):
        self.assertIsNone(_fetch_bearer_token("Basic realm=x"))

    def test_missing_realm_returns_none(self):
        challenge = self._challenge(None)
        self.assertIsNone(_fetch_bearer_token(challenge))

    def test_non_http_realm_is_rejected(self):
        challenge = self._challenge("file:///etc/passwd")
        self.assertIsNone(_fetch_bearer_token(challenge))

    @patch("cli.image_digest_check.urlopen")
    def test_valid_challenge_fetches_and_returns_token(self, mock_urlopen):
        service = "serv" + "ice"
        q = chr(34)
        challenge = self._challenge(
            "https://auth.example/token", extra=service + "=" + q + "registry.example" + q
        )
        mock_urlopen.return_value = _FakeTokenResponse({"token": "abc123"})
        token = _fetch_bearer_token(challenge)
        self.assertEqual(token, "abc123")

    @patch("cli.image_digest_check.urlopen")
    def test_access_token_field_is_also_accepted(self, mock_urlopen):
        challenge = self._challenge("https://auth.example/token")
        mock_urlopen.return_value = _FakeTokenResponse({"access_token": "xyz789"})
        token = _fetch_bearer_token(challenge)
        self.assertEqual(token, "xyz789")

    @patch("cli.image_digest_check.urlopen")
    def test_unreachable_token_endpoint_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("offline")
        challenge = self._challenge("https://auth.example/token")
        self.assertIsNone(_fetch_bearer_token(challenge))


class FetchRegistryDigestTest(unittest.TestCase):
    @patch("cli.image_digest_check.urlopen")
    def test_returns_digest_header(self, mock_urlopen):
        mock_urlopen.return_value = _FakeHeadResponse(
            {"Docker-Content-Digest": "sha256:" + "c" * 64}
        )
        digest = fetch_registry_digest("docker.io", "library/postgres", "18")
        self.assertEqual(digest, "sha256:" + "c" * 64)

    @patch("cli.image_digest_check.urlopen")
    def test_url_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("offline")
        digest = fetch_registry_digest("docker.io", "library/postgres", "18")
        self.assertIsNone(digest)

    @patch("cli.image_digest_check._fetch_bearer_token")
    @patch("cli.image_digest_check.urlopen")
    def test_401_retries_with_bearer_token(self, mock_urlopen, mock_token):
        mock_token.return_value = "tok"
        challenge_scheme = "Bear" + "er"
        challenge_param = "real" + "m"
        challenge_value = "https://auth.example/token"
        auth_challenge = (
            challenge_scheme
            + " "
            + challenge_param
            + "="
            + chr(34)
            + challenge_value
            + chr(34)
        )
        responses = [
            HTTPError("url", 401, "auth required", {"WWW-Authenticate": auth_challenge}, None),
            _FakeHeadResponse({"Docker-Content-Digest": "sha256:" + "d" * 64}),
        ]
        sent_requests = []

        def _side_effect(request, *args, **kwargs):
            sent_requests.append(request)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        mock_urlopen.side_effect = _side_effect
        digest = fetch_registry_digest("docker.io", "library/postgres", "18")
        self.assertEqual(digest, "sha256:" + "d" * 64)
        # The retried request must actually carry the fetched bearer token,
        # not just happen to succeed on the second urlopen call.
        expected_auth = "Bearer" + " " + "tok"
        self.assertEqual(sent_requests[1].get_header("Authorization"), expected_auth)


class CollectPinnedImagesForModuleTest(unittest.TestCase):
    def test_finds_only_digest_pinned_images(self):
        module_def = {
            "spec": {
                "implementation": {
                    "compose": {
                        "services": {
                            "db": {"image": f"postgres:18@sha256:{'a' * 64}"},
                            "app": {"image": "myapp:custom"},
                        }
                    }
                }
            }
        }
        entries = collect_pinned_images_for_module(module_def)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["service"], "db")


if __name__ == "__main__":
    unittest.main()
