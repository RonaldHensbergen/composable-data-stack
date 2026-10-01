# Compliance/evidence report

`cds report` exports a single, exportable snapshot of the readiness evidence
CDS already computes for a rendered stack, so you don't have to piece it
together by hand from `cds plan`, `cds security --verify-images`, and the
signed-images fixture. It covers issue #734.

> **Disclaimer:** This report aggregates readiness evidence that CDS already
> computes for a rendered stack (module/image identification, linked
> signature/SBOM/provenance attestation status, contract topology, and a
> rendered-secret-leak check). **It is not a legal compliance or conformity
> certification** — confirm applicability against your own regulatory
> obligations. It also has nothing to do with the `images/` vulnerability
> scanning/patching process (see [image-scanning.md](image-scanning.md)) or
> the image signing pipeline itself (see [image-signing.md](image-signing.md));
> it only *reads* evidence those processes already produced.

## What's in the report

| Section | Content |
| --- | --- |
| `modules` | Resolved module id, source, version, and `dependsOn` for every module instance in the plan. |
| `images` | Every image referenced by the rendered Compose output, whether it is digest-pinned, and linked signature/SBOM/provenance evidence looked up from the signed-images fixture (see [image-signing.md](image-signing.md#offline-verification)). |
| `topology` | Each module's provided and consumed contracts (with the resolved `contractRef` and provider module) and its `dependsOn` edges. |
| `secretLeakCheck` | Confirms none of the profile's declared secret values (`spec.secrets.values`) appear literally in the rendered Compose output — only `${CDS_*}` placeholders should. |
| `disclaimer` | The exact text above, always included verbatim. |

Missing evidence never fails the report outright — it degrades gracefully:

- A locally-built image (`local/<name>:custom` with a build context) is
  reported `not-available` with reason "Locally built image; not published,
  so no signature/SBOM/provenance evidence exists yet." and **no** warning
  diagnostic, since this is an expected development-time state.
- A published, digest-pinned image with no matching fixture entry is
  reported `not-available` with a `W101` warning diagnostic, since evidence
  *should* be linkable but currently isn't (fixture stale, wrong registry,
  etc.).
- No signed-images fixture configured at all also produces a `not-available`
  entry with a distinct reason, plus a `W101` warning.

A leaked secret value is always an `E119` error diagnostic and sets
`secretLeakCheck.status` to `fail`; `cds report`'s exit code is `1` in that
case (and whenever validate/plan/render itself fails), `0` otherwise.

## Usage

```bash
# Human-readable summary to stdout
cds report profiles/<name>/profile.yaml

# Machine-readable JSON, e.g. for archiving alongside a release
cds report <profile-name> --json --output evidence-report.json

# Apply the same environment/hardened/image-source overrides as cds test/up
cds report <profile-name> --environment prod --image-source registry
```

`cds report` accepts the same `profile`, `--environment`/`-e`, `--hardened`,
and `--image-source` arguments as `cds test`/`cds up` (see `cds report --help`
for the full reference), plus:

- `--json` — output JSON instead of the human-readable text summary.
- `--output`/`-o` — save the report to a file instead of printing it.

Diagnostics (warnings/errors) are always printed to **stderr**, so `--json`
output on stdout stays parseable even when the report has warnings.

## Example JSON shape

```json
{
  "apiVersion": "cds/v1alpha1",
  "kind": "ComplianceReport",
  "generatedAt": "2026-09-27T09:49:56+00:00",
  "profile": {
    "sourceProfile": "profiles/local-dagster-postgres-superset/profile.yaml",
    "name": "local-dagster-postgres-superset",
    "environment": null
  },
  "modules": [
    {"id": "postgres", "source": "../../modules/warehouse/postgres", "version": "0.1.0", "dependsOn": []}
  ],
  "images": [
    {
      "service": "postgres",
      "image": "postgres:18@sha256:...",
      "digestPinned": true,
      "evidence": {"status": "not-available", "reason": "..."}
    }
  ],
  "topology": [
    {"id": "postgres", "dependsOn": [], "provides": [{"name": "sql-database", "kind": "sql-database"}], "consumes": []}
  ],
  "secretLeakCheck": {"status": "pass", "checkedSecretCount": 6, "leakedSecretNames": []},
  "disclaimer": "This report aggregates readiness evidence ..."
}
```

## Limitations

- Only covers Compose-rendered stacks (`cds render`'s default target);
  Helm-rendered stacks aren't currently reported on.
- Image evidence is only as fresh as `tests/fixtures/signed-images.json`
  (or a custom fixture pointed to by `CDS_SIGNED_IMAGES_FIXTURE`); it does
  not perform a live cosign/registry lookup.
- The secret-leak check only scans for the *literal values* of secrets the
  profile declared under `spec.secrets.values`; it cannot detect a
  secret leaked under a different, unrelated string.
