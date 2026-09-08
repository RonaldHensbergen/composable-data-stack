import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.utils import _atomic_write


class AtomicWriteTest(unittest.TestCase):
    def test_writes_content_to_target_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "nested" / "output.txt"
            _atomic_write(target, "hello world")
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "a" / "b" / "c" / "output.txt"
            _atomic_write(target, "nested")
            self.assertTrue(target.parent.is_dir())

    def test_replace_failure_cleans_up_temp_file_and_reraises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "output.txt"
            captured_tmp_names = []
            real_mkstemp = tempfile.mkstemp

            def _spying_mkstemp(*args, **kwargs):
                fd, name = real_mkstemp(*args, **kwargs)
                captured_tmp_names.append(name)
                return fd, name

            with (
                patch("cli.utils.tempfile.mkstemp", side_effect=_spying_mkstemp),
                patch("cli.utils.os.replace", side_effect=OSError("replace failed")),
            ):
                with self.assertRaises(OSError):
                    _atomic_write(target, "content that should not persist")

            self.assertFalse(target.exists())
            self.assertEqual(len(captured_tmp_names), 1)
            self.assertFalse(os.path.exists(captured_tmp_names[0]))

    def test_replace_failure_when_temp_file_already_removed_does_not_mask_original_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "output.txt"

            def _failing_replace(src, dst):
                os.unlink(src)
                raise OSError("replace failed after unlink")

            with patch("cli.utils.os.replace", side_effect=_failing_replace):
                with self.assertRaises(OSError):
                    _atomic_write(target, "content")

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
