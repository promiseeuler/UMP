# UMP Versioning Policy

The protocol specification, capability profiles, SDKs, and runtime are versioned independently using semantic versioning.

- A protocol major release may introduce incompatible wire or normative behavior.
- A protocol minor release may add optional fields, messages, or negotiated features.
- A protocol patch release clarifies text or corrects behavior without changing compatibility.
- Capability profile versions do not force a core protocol version change when extension rules are obeyed.

Protocol Buffers field numbers are permanent. Removed fields are reserved by name and number. Existing field meaning, units, cardinality, or security effect cannot change incompatibly within a major version.

Experimental features use namespaced identifiers, are excluded from core conformance, and cannot become mandatory without a UEP and compatibility plan.

The current repository and `ump.v1` wire package are pre-release. Until Protocol 1.0 is frozen, drafts may change incompatibly with a documented migration note.

The reference runtime currently advertises draft protocol `1.2` and accepts a
configured compatibility ceiling down to `1.0`. Negotiation selects the highest common
minor, and commands remain gated by mutually advertised capabilities and
features. Gateway proxy descriptors require `1.2`; their configured minimum
prevents an older peer from silently interpreting a proxy as direct hosting.
This development compatibility mode is tested but is not a claim of
interoperability with an independently implemented historical release.
