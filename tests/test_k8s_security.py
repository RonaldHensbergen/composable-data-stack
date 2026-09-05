import unittest

from cli.k8s_security import scan_k8s_security


class KubernetesSecurityTest(unittest.TestCase):
    def secure_plan(self) -> dict:
        return {
            "modules": [
                {
                    "id": "demo",
                    "implementation": {
                        "compose": {
                            "services": {
                                "app": {
                                    "read_only": True,
                                    "cap_drop": ["ALL"],
                                    "security_opt": ["no-new-privileges:true"],
                                }
                            }
                        },
                        "kubernetes": {
                            "workloads": {
                                "app": {
                                    "kind": "Deployment",
                                    "containers": ["app"],
                                    "podSecurityContext": {
                                        "runAsNonRoot": True,
                                        "runAsUser": 1000,
                                    },
                                    "resources": {
                                        "app": {
                                            "requests": {"cpu": "10m", "memory": "16Mi"},
                                            "limits": {"memory": "32Mi"},
                                        }
                                    },
                                }
                            }
                        },
                    },
                }
            ]
        }

    def test_secure_workload_has_no_findings(self) -> None:
        self.assertEqual(scan_k8s_security(self.secure_plan()), [])

    def test_insecure_workload_reports_every_kubernetes_guard(self) -> None:
        plan = self.secure_plan()
        service = plan["modules"][0]["implementation"]["compose"]["services"]["app"]
        service.clear()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]
        workload["podSecurityContext"] = {"runAsUser": 0}
        workload["resources"] = {"app": {"requests": {"cpu": "10m"}}}

        findings = scan_k8s_security(plan)

        self.assertEqual(
            {finding["rule_id"] for finding in findings},
            {
                "CDS-K8S-001",
                "CDS-K8S-002",
                "CDS-K8S-003",
                "CDS-K8S-004",
                "CDS-K8S-005",
            },
        )

    def test_container_override_can_supply_security_context(self) -> None:
        plan = self.secure_plan()
        plan["modules"][0]["implementation"]["compose"]["services"]["app"].clear()
        workload = plan["modules"][0]["implementation"]["kubernetes"]["workloads"]["app"]
        workload["containerOverrides"] = {
            "app": {
                "securityContext": {
                    "readOnlyRootFilesystem": True,
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                }
            }
        }

        self.assertEqual(scan_k8s_security(plan), [])


if __name__ == "__main__":
    unittest.main()
