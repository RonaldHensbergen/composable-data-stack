import json
import unittest
from unittest.mock import patch

from cli.image_updates import fetch_dockerhub_tags


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
