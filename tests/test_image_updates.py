import json
import unittest
from unittest.mock import patch

from cli.image_updates import fetch_dockerhub_tags
from cli.image_updates import parse_image_reference


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


if __name__ == "__main__":
    unittest.main()
