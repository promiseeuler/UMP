# UMP Standard Capability Vocabulary v1

`ump.standard/v1` is the first small manufacturer-neutral vocabulary for the
reference warehouse scenario. Its canonical machine-readable catalog is shipped
at `ump/vocabulary_data/v1/catalog.json` in the Python wheel and source archive.

The vocabulary contains:

- `ump.navigation.inspect-route/v1`: inspect traversability between two named
  semantic locations;
- `ump.material.carry/v1`: carry a named object to a named semantic destination;
- `ump.manipulation.place/v1`: place a named object at a named semantic target.

These are high-level desired outcomes. They do not define paths, joint commands,
gripper commands, controller gains, or emergency behavior. A manufacturer adapter
accepts or rejects each assignment and remains responsible for native planning,
control, limits, transforms, and safety.

## Structured results

Every capability requires a bounded machine-readable result. Inspect-route
reports whether the route was traversable. Carry reports delivery and final
semantic location. Place reports placement and target. Optional summaries remain
human-readable. `Outcome.outputs` is limited to 16 KiB and validated against the
advertised output schema before it becomes durable terminal evidence.

A malformed adapter result becomes `unknown`; UMP does not silently discard bad
fields while retaining success. `completed: true` means the adapter returned a
schema-conforming terminal result. It does not independently prove physical work
or replace owner/manufacturer evidence and safety processes.

## Tooling

```sh
ump-vocabulary list
ump-vocabulary show ump.material.carry/v1
ump-vocabulary validate-input ump.material.carry/v1 carry-request.json
ump-vocabulary validate-output ump.material.carry/v1 carry-result.json
```

Use `-` instead of a path to read a JSON document from standard input. Commands
emit JSON and return exit status `2` for an unknown capability, unreadable JSON,
or schema violation.

## Compatibility

Capability names include their major contract version. Compatible clarifications
may update catalog prose and tighten implementation guidance without changing the
wire shape. A breaking input, output, or behavioral change requires a new suffix,
such as `/v2`, and must coexist explicitly with `/v1` during migration.

Vendor capabilities remain allowed under vendor-owned namespaces. They receive
the same schema, unit, frame, authority, and result-validation protections but are
not represented as standard UMP semantics.
