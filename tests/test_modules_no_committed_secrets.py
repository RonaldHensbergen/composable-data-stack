import glob
import unittest
import warnings
from pathlib import Path


class TestModulesNoCommittedSecrets(unittest.TestCase):
    def test_module_templates_do_not_reference_secrets(self):
        patterns = ["modules/**/module.yaml", "modules-experimental/**/module.yaml"]
        offenders = []
        for pattern in patterns:
            for path in glob.glob(pattern, recursive=True):
                p = Path(path)
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError as exc:
                    warnings.warn(f"Skipping unreadable module template {p}: {exc}", stacklevel=2)
                    continue
                if "secrets." in text:
                    offenders.append(str(p))
        self.assertFalse(offenders, f"Module templates must not reference 'secrets.': {offenders}")


if __name__ == "__main__":
    unittest.main()
