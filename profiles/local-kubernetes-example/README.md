# Local Kubernetes example

The `local-dagster-postgres-superset` stack rendered to Kubernetes instead of
Docker Compose (issue #77). The profile extends the base profile unchanged —
CDS picks the render target at the CLI, not in the profile:

```bash
cds validate local-kubernetes-example --target helm
cds render local-kubernetes-example --target helm   # writes chart/
```

Run it on the isolated local k3d cluster:

```bash
make k3d-build                                        # build module images
PROFILE=profiles/local-kubernetes-example/profile.yaml make k3d-up k3d-install
```

See [docs/kubernetes.md](../../docs/kubernetes.md) for the full workflow
(secrets at install time, translation boundaries, network policies,
troubleshooting).
