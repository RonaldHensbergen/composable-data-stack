# PR #608 change-request ledger

Temporary review ledger for [PR #608](https://github.com/RonaldHensbergen/composable-data-stack/pull/608),
compiled 2026-09-07. It separates merge-blocking or actionable requests from
explicitly non-blocking follow-ups and informational comments.

## Actionable requests 

| Priority | Location | Request | Source |
| --- | --- | --- | --- |
| Critical | `scripts/run_tests_with_deprecation_gate.py:55` | On platforms without `SIGALRM`/`setitimer`, including Windows, make per-test timeouts a warning-backed no-op rather than raising before tests run. | [Inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3933940761) |
| Critical | `cli/k8s_renderer.py:958` | Restrict `configMaps.*.fromBind` to an explicit repository-local root. Reject absolute paths, traversal, and symlink escapes before file contents enter rendered ConfigMaps. | [Inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3940767071) |
| High | `cli/k8s_security.py:27` | Inspect each container's effective security context, including `containerOverrides.*.securityContext`, so container-level root overrides cannot bypass pod-level non-root checks. | [Inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3940767074) |
| Medium | `cli/main.py:1096` | Add `--target` to `cds test`; for Helm, render the chart and run Kubernetes security checks rather than validating only Compose output. | [Inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3933940770) |
| Medium | `cli/k8s_renderer.py:1113` | Release-scope Helm Service names and resolve `${k8s.service.*}` to the matching release-scoped DNS name, allowing multiple releases per namespace. | [Inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3940767077) |
| Medium | `cli/k8s_renderer.py:1220`, `cli/k8s_renderer.py:1222` | Remove globally ambiguous short service aliases, scope them per module, or raise a collision diagnostic. Both comments report the same silent-overwrite issue when modules use the same workload name. | [First inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3933940775); [second inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3940767079) |
| Medium | `scripts/tender/provision-dashboard.py:113` | Build `analytics_uri()` from `CDS_ANALYTICS_DB_CONNECTION_URI` or the exported host/port binding variables instead of hardcoding `postgres:5432`. | [Inline review](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#discussion_r3940767085) |

## Explicitly non-blocking follow-ups

| Area | Follow-up | Source |
| --- | --- | --- |
| Roadmap traceability | Link or account for the Kubernetes roadmap epic #65 and its related issues #73 through #77. The review identifies partial scope for profile runtime selection, NetworkPolicies, live-cluster CI, a migration guide, and a production-style profile. | [Review comment](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#issuecomment-5540922641) |
| Scope management | NetworkPolicy generation (#75), live kind/k3d CI (#76), and migration/production-profile documentation (#77) may be deferred into follow-up issues rather than expanding this PR. | [Review comment](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#issuecomment-5540931647) |
| Lifecycle consistency | Unify Compose and Helm readiness behavior, make Helm handling for `--no-build`/`--no-color` explicit, apply `--timeout` as one overall Helm budget, and share namespace/release derivation. | [Review comment](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#issuecomment-5546078244) |
| Helm self-sufficiency | Consider bringing image building, cluster preparation, and image import into the Kubernetes lifecycle, which currently only renders and installs into a pre-prepared cluster. | [Review comment](https://github.com/RonaldHensbergen/composable-data-stack/pull/608#issuecomment-5546103309) |
| Security | Replace SHA-1 (`shasum`) with a modern hash for deterministic branch-to-port mapping, or explicitly document why a non-cryptographic hash is sufficient. This is not security-sensitive because it only derives local ports. | SonarCloud `shell:S4790` |

## Informational comments

Other comments are informational or already included in the lists above.