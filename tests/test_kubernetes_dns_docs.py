import unittest
from pathlib import Path


class KubernetesDnsDocsTest(unittest.TestCase):
    def test_dns_egress_selector_and_distro_workaround_are_documented(self) -> None:
        docs = (
            Path(__file__).resolve().parent.parent / "docs" / "kubernetes.md"
        ).read_text(encoding="utf-8")

        self.assertIn("`k8s-app: kube-dns`", docs)
        self.assertIn("OpenShift's `dns-default`", docs)
        self.assertIn("implementation.kubernetes.networkPolicy.egress", docs)
        self.assertIn("both UDP and TCP port 53", docs)


if __name__ == "__main__":
    unittest.main()
