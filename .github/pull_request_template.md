# Pull Request

## Summary

Describe what changed and why.

## Type Of Change

- [ ] Bug fix (`fix:` — a defect in code that is live)
- [ ] Feature (`feat:`)
- [ ] Refinement (`refine:` — polishing work that was never shipped as a defect; labeled honestly so throughput metrics stay truthful)
- [ ] Refactor (`refactor:`)
- [ ] Docs (`docs:`)
- [ ] Test only (`test:`)

## User Impact

What should users notice after this change?

## Validation

List commands run and outcomes.

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## Checklist

- [ ] Tests added or updated
- [ ] Docs updated (README or docs)
- [ ] No secrets committed
- [ ] Generated artifacts excluded from git
- [ ] Success criteria from the linked issue are met and demonstrated in Validation
- [ ] Contract kept current: `module.yaml`/profile YAML still validate against `cli/resources/*.schema.json`, and docs match the new behavior
- [ ] Existing behavior was pinned by a test (golden or characterization) before the code was changed
- [ ] Breathe-out done in this same PR: learnings folded back into the existing code, no new technical debt
- [ ] Every added test earns its place: it catches a mutant no other test catches (kill matrix, `make test-mutation`)
- [ ] Bug fixes include a regression test
- [ ] Branch is short-lived and rebased on `main` (integrate continuously)
