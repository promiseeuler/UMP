# Requirement Traceability

`compliance/requirements.json` maps every named functional requirement in the
PRD to an explicit `implemented`, `partial`, or `missing` status, concrete
repository evidence, and a short assessment. The readiness audit fails closed
when a PRD identifier is added or removed without updating the matrix, when IDs
are duplicated, when evidence paths do not exist, or when an assessment is
missing.

Run the current readiness assessment with:

```sh
ump-readiness
```

Exit status `0` means every requirement is implemented. Exit status `1` means
the matrix is valid but at least one requirement remains partial or missing.
Exit status `2` means the matrix itself cannot be trusted.

CI uses structural validation while unfinished product work remains visible:

```sh
ump-readiness --validate-only
```

Validation-only mode does not claim product readiness. It returns success only
for matrix integrity and still emits the real `ready` value and status counts.
Requirement statuses should change only with direct implementation and test
evidence, never because adjacent functionality appears similar.
