# Security finding compliance-control categories

`cds security` and `cds test` evaluate the rule set in
`cli/resources/rule-set.json` (validated against
`cli/resources/rule-schema.json`) plus a handful of checks enforced directly
in code (`cli/k8s_security.py`, `cli/image_verification.py`). Every finding
those checks produce carries a `category` tag from the closed set documented
below.

## Disclaimer

**These categories are an informational readiness aid, not a legal or
regulatory certification.** They exist to help users organize CDS findings
onto the control categories their own risk assessments or audits (for
example under NIS2, the Cyberbeveiligingswet, or an internal security
framework) are structured around. A category tag on a rule does not mean
that rule alone satisfies, or is required by, any specific law, standard, or
certification scheme. Users remain responsible for their own compliance
assessment.

## Category set

| Category | Label | What it covers |
| --- | --- | --- |
| `secrets` | Secrets management | Hardcoded, weak, reused, or leaked credentials and other secret-like values. |
| `auth` | Access control | Authentication/authorization posture, including credential reuse across services. |
| `network-exposure` | Network exposure | Published ports, plaintext endpoints, and reachability from outside the intended network boundary. |
| `file-output-safety` | File output safety | Unsafe permissions or unsafe destinations for rendered/generated files. |
| `config-integrity` | Configuration integrity | Internal consistency and safe defaults of profile/module configuration. |
| `supply-chain` | Supply chain | Image provenance, signature/attestation verification, registry trust, and version/digest pinning. |
| `runtime-hardening` | Runtime hardening | Container/pod runtime posture, e.g. non-root users, dropped capabilities, and resource limits. |
| `observability-leakage` | Logging & monitoring leakage | Secrets or sensitive data leaking through logs, healthchecks, or diagnostics output. |

This set is the single source of truth in code: `cli.security_common.RULE_CATEGORIES`.
Every rule in `rule-set.json` is required (by `rule-schema.json`) to declare
exactly one of these categories; schema validation rejects a rule with a
missing or unrecognized value. The code-enforced checks in
`cli/k8s_security.py` (`CDS-K8S-*`) and `cli/image_verification.py`
(`CDS-SEC-050`/`051`/`052`, `CDS-VER-*`) aren't declared in `rule-set.json`
(see [`docs/image-signing.md`](image-signing.md) for why the image checks
aren't declarative rule-set entries) and instead assign one of the same
category values directly at their finding call sites.

## Grouping and filtering findings

`cds security` and `cds test` accept a repeatable `--category <name>` flag to
only display findings in the given categories, and `cds security` accepts
`--group-by-category` to print findings under a heading per category:

```bash
cds security my-profile --category secrets --category network-exposure
cds security my-profile --group-by-category
```

Both flags are display-only: they never change which rules run, nor a
finding's severity or pass/fail outcome. The command's exit code (and, for
`cds test`, the security stage's PASS/FAIL result) is always computed from
the full, unfiltered set of findings.
