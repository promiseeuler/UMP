# Interoperability Profile

UMP capability contracts use JSON Schema Draft 2020-12 plus two bounded
annotations for physical numeric fields.

## Units

Every schema node whose type includes `number` or `integer` must declare
`x-ump-unit`. Dimensionless ratios, counters, and indexes use `1`. The v0.1
robotics SI profile accepts:

```text
1, m, m^2, m^3, m/s, m/s^2,
rad, rad/s, rad/s^2, s, Hz,
kg, kg/m^3, N, N*m, Pa, K, A, V, W, J
```

Unit conversion belongs at the manufacturer adapter boundary. A UMP peer must
not infer units from field names, prose, vendor defaults, or coordinate values.

## Frames

An object containing a numeric field with a spatial unit must declare
`x-ump-frame-field`. Its value names a property on that object, and that property
must be a required string. For example:

```json
{
  "type": "object",
  "x-ump-frame-field": "frame_id",
  "required": ["distance", "frame_id"],
  "properties": {
    "distance": {"type": "number", "x-ump-unit": "m"},
    "frame_id": {"type": "string"}
  }
}
```

UMP names frames but does not distribute transforms or assume ROS TF ownership.
Adapters must resolve transforms in native software and reject requests whose
frame cannot be resolved safely.

## Enforcement

`Capability` validates annotations recursively in input/output properties,
array items, composition branches, and `$defs`. Therefore locally constructed
manifests and network-decoded manifests fail before planning or native execution
when numeric semantics are ambiguous. The adapter conformance harness also
checks the complete JSON Schema structure.

The unit and frame rules apply to both standard and vendor capabilities. The
first bounded domain vocabulary is specified in `VOCABULARY.md`; it standardizes
three high-level warehouse outcomes without standardizing physical control.
