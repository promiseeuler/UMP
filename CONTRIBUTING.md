# Contributing to UMP

Keep changes focused on UMP's protocol, awareness, adapter, networking,
collaboration, conformance, or inspector boundaries. New simulation engines,
fleet managers, and vendor-specific control logic belong in separate
integrations rather than the protocol core.

## Development checks

```sh
python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m ump.cli conformance conformance/v0.1
```

## Conventional Commits

All commits must use the Conventional Commits form:

```text
<type>(optional-scope): <imperative summary>
```

Allowed types are `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`,
`ci`, `chore`, and `revert`. Use `!` or a `BREAKING CHANGE:` footer for an
incompatible protocol or public API change.

Examples:

```text
feat(protocol): add battery status to robot state
fix(network): expire disconnected participant state
docs(reference): map UMP fields to MassRobotics concepts
refactor(core)!: remove deprecated simulation API
```

## Filename conventions

- Python modules and packages: lowercase `snake_case.py`.
- Python tests: `test_<subject>.py`.
- JSON Schemas: lowercase kebab-case with a version suffix, such as
  `ump-robot-state-v1.schema.json`.
- Executable scripts: lowercase kebab-case when exposed as commands; use
  `snake_case.py` for Python source files.
- Normative and project-level Markdown: uppercase names, such as `README.md`,
  `REFERENCE.md`, and `SECURITY.md`.
- Topic documentation under `docs/`: uppercase `SNAKE_CASE.md`, matching the
  existing documentation set.
- Static web assets: lowercase conventional names such as `index.html`,
  `styles.css`, and `app.js`.

Do not introduce spaces in source, schema, test, or documentation filenames.

## Compatibility changes

Protocol changes require an updated canonical schema, model validation, golden
vectors, compatibility notes, and tests. Integrations with external standards
must remain isolated behind adapters and must document their source standard and
license in `REFERENCE.md`.
