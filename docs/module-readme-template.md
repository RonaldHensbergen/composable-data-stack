# Module README template

Standard format for the optional `README.md` file inside each module directory.

`README.md` is for human readers only and is never read by the validator.
Keep it to context that does not belong in YAML, such as rationale, known
limitations, and links to upstream docs. The authoritative module reference
is `module.yaml`, see [docs/modules.md](modules.md#documentation) for the
full module authoring guide.

## Template

Copy this template into `modules/<category>/<name>/README.md` and replace
the placeholders. Keep the section order.

````markdown
# <Display name>

<One-line description matching metadata.description in module.yaml.>

## Purpose

<Short paragraph. What the module does and when a profile should use it.>

## Known limitations

- <limitation or operational caveat>
- <limitation or operational caveat>

## Upstream documentation

- [<Project> documentation](<link to official docs>)

## Configuration notes

<Anything not obvious from the config schema, such as runtime behavior or gotchas.>
````

## Guidance

- Match `metadata.displayName` and `metadata.description` from the module's `module.yaml`.
- Sections other than `Purpose` are optional when they would be empty.
- Prefer prose over config tables, the config schema already documents config keys.
- See `modules/secrets/vault/README.md` for a completed example.
