# Security rule-set compliance control categories

Every rule in `cli/resources/rule-set.json` carries two category-shaped
fields:

- `category`: an internal engineering taxonomy used to group rules by the
  part of `cli/security.py` that evaluates them (e.g. `secrets`, `auth`,
  `runtime-hardening`). This is not meant to be user-facing.
- `complianceCategory`: an informational tag mapping the rule's finding onto
  a control area commonly used in risk assessments and security audits (e.g.
  NIS2, the Cyberbeveiligingswet, ISO 27001 Annex A, or an internal control
  framework). This is the field `cds security`/`cds test`'s `--category` and
  `--group-by-category` flags operate on.

`rule-schema.json` defines `complianceCategory` as a required, closed enum
(`$defs.complianceCategory`); schema validation rejects a rule with a
missing or unrecognized value.

## Disclaimer

**These categories are an informational readiness aid to help you organize
CDS security findings against your own compliance risk assessment. They are
not a legal or regulatory certification of any rule, category, or of CDS
itself, and they do not represent an exhaustive or authoritative mapping to
any specific law, standard, or framework.** Always consult your own
compliance/legal function to determine which controls apply to your
organization and whether CDS's checks satisfy them.

## Category set and rationale

| Category | Rationale |
| --- | --- |
| `access-control` | Rules about authentication being present and adequately strong (default/missing credentials, weak secret strength, secret reuse across services that weakens per-service access control). |
| `secrets-management` | Rules about how secret material is stored, referenced, and handled (hardcoded secrets, secrets embedded in DSNs/URLs, inline key material, secrets leaking into generated files or file permissions). |
| `network-exposure` | Rules about services being reachable from more of the network than intended (binding to `0.0.0.0`, externally published databases, non-local interfaces in a local profile). |
| `encryption-in-transit` | Rules specifically about traffic confidentiality/integrity in transit (an authenticated service or a production endpoint served over plain HTTP without a TLS reverse-proxy). Kept separate from `network-exposure` because "who can reach it" and "is the channel encrypted" are usually assessed as distinct controls. |
| `configuration-management` | Rules about configuration correctness and integrity (insecure fallback defaults, empty required secrets, unapproved secret binding targets, profile inheritance weakening secure defaults, non-production-suitable modules used in staging/production). |
| `system-hardening` | Rules about container/runtime hardening (running as root, privileged/excess capabilities, writable root filesystem, sensitive host path mounts). |
| `logging-monitoring` | Rules about security-relevant information not leaking into (or via) logs, command lines, or CLI output, which undermines the same logging/monitoring control it's meant to support. |
| `patching` | Reserved for future image/dependency freshness rules (e.g. an `imageTagPolicy` rule requiring pinned or digest-verified images). No rule uses it yet. |

## Grouping and filtering findings

```bash
cds security <profile> --group-by-category
cds security <profile> --category secrets-management --category access-control
cds test <profile> --group-by-category
```

`--category` is repeatable and only accepts values from the closed set
above; it does not change which rules run or their pass/fail outcome, only
which findings are printed and the exit code that follows from that
(narrower) set. Findings produced outside of `rule-set.json` (e.g.
`--target=helm` Kubernetes checks or `--verify-images` image verification
findings) have no compliance category; they are grouped under
`uncategorized` by `--group-by-category` and excluded by `--category`.
