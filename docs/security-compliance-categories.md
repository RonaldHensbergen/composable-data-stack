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
missing or unrecognized value. `cli.security_common.COMPLIANCE_CATEGORIES`
is the single source of truth in code for the category set, each category's
display label, and its rationale (the same rationale documented in the
table below); `cds security --group-by-category` uses it to render a
`"<label> (<category>)"` heading per group.

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
| `configuration-management` | Rules about configuration correctness and integrity (insecure fallback defaults, empty required secrets, unapproved secret binding targets, profile inheritance weakening secure defaults, non-production-suitable modules used in staging/production). Also covers `cli/k8s_security.py`'s `CDS-K8S-005` (missing resource requests/limits), which isn't declared in `rule-set.json` but is a configuration-completeness concern rather than runtime-hardening posture. |
| `system-hardening` | Rules about container/runtime hardening (running as root, privileged/excess capabilities, writable root filesystem, sensitive host path mounts). Also covers `cli/k8s_security.py`'s pod/container posture checks `CDS-K8S-001`-`004`, which aren't declared in `rule-set.json` but are tagged with this category directly at their source since they check the same kind of runtime posture. |
| `logging-monitoring` | Rules about security-relevant information not leaking into (or via) logs, command lines, or CLI output, which undermines the same logging/monitoring control it's meant to support. |
| `patching` | Image/dependency freshness, provenance, and trust: tag/digest pinning, registry trust, and cosign signature/build-provenance verification. Also covers the `cli/image_verification.py` checks (`CDS-SEC-050`/`051`/`052`, `CDS-VER-*`), which aren't declared in `rule-set.json` (see [`docs/image-signing.md`](image-signing.md) for why) but are tagged with this category directly at their source. |

## Grouping and filtering findings

```bash
cds security <profile> --group-by-category
cds security <profile> --category secrets-management --category access-control
cds test <profile> --group-by-category
```

`--category` is repeatable and only accepts values from the closed set
above; it is a display-only filter and never changes which rules run,
their pass/fail outcome, or the command's exit code -- a high-severity
finding outside the requested category still fails the scan. Findings
produced outside of `rule-set.json` (`--target=helm` Kubernetes checks and
`--verify-images` image verification findings) are also tagged with a
category directly at their source (`system-hardening`/`configuration-management`
and `patching` respectively, see the table above) so they participate in
`--category` filtering and `--group-by-category` grouping like any other
finding.
