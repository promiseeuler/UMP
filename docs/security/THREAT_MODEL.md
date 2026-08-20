# UMP Initial Threat Model

**Status:** Phase 0 baseline  
**Method:** Assets, trust boundaries, attacker capabilities, threats, controls, and verification

## 1. Safety boundary

UMP is a communication and coordination protocol, not a certified functional-safety system. A local robot controller, PLC, flight controller, or certified safety controller remains authoritative for actuator limits, interlocks, collision controls, emergency stopping, and recovery permission.

A validly authenticated UMP command is a request, not proof that execution is physically safe. The adapter MUST submit it through native policy and safety checks.

## 2. Protected assets

- Stable machine identity and private credentials.
- Authority to invoke physical-effect capabilities.
- Integrity and freshness of task, lease, resource, handoff, and safety messages.
- Availability of control traffic and local machine resources.
- Accuracy of peer, health, spatial, and ownership state.
- Confidentiality of capabilities, facility topology, telemetry, and operational history.
- Audit evidence needed to reconstruct responsibility.

## 3. Trust boundaries

```text
Remote peer
  | authenticated encrypted transport
UMP runtime
  | authenticated local adapter API
Machine adapter
  | vendor-supported native interface
Robot/controller
  | certified or local safety boundary
Physical actuators and environment
```

Gateways add another boundary between the gateway host and represented machine. Cloud registries and coordinators are optional remote peers, not implicitly trusted infrastructure.

## 4. Attacker capabilities

The design assumes an attacker may:

- join or observe the local network;
- capture, replay, reorder, duplicate, delay, truncate, or modify packets;
- claim arbitrary unauthenticated identity fields;
- send malformed, oversized, deeply nested, or high-rate messages;
- possess valid credentials for a different machine or expired role;
- compromise one authorized peer and attempt lateral movement;
- cause clock skew, network partitions, or repeated reconnects;
- compromise a non-isolated adapter or gateway process; and
- obtain logs or crash dumps containing secrets.

The initial model does not guarantee operation after full compromise of the represented robot controller, its operating-system kernel, or its hardware root of trust.

## 5. Threats and required controls

| ID | Threat | Required control | Verification |
|---|---|---|---|
| `T-01` | Machine impersonation | Mutual authentication bound to stable machine identity | Wrong and untrusted certificates rejected |
| `T-02` | Command tampering | Authenticated encryption and schema validation | Modified ciphertext/session fails closed |
| `T-03` | Replay or duplicate execution | Session identity, sequence/freshness checks, idempotency | Captured command cannot execute twice |
| `T-04` | Protocol downgrade | Version negotiation protected inside authenticated session | Active downgrade produces rejection |
| `T-05` | Stolen or stale credential | Rotation, expiry, revocation, scoped authorization | Revoked identity cannot reconnect or invoke |
| `T-06` | Compromised peer moves laterally | Capability-level least privilege and deny-by-default policy | Peer cannot access ungranted capability |
| `T-07` | Message/resource exhaustion | Frame, rate, concurrency, memory, and journal limits | Fuzz and saturation do not crash runtime |
| `T-08` | Bulk data blocks control | Separate bounded control and data paths | Control latency remains bounded under load |
| `T-09` | False safety state | Authenticated source, freshness, explicit unknown state | Stale/unauthorized safety signal rejected |
| `T-10` | Gateway misrepresentation | Explicit proxy identity and machine association | Inspector and peer can distinguish proxy |
| `T-11` | Secret disclosure | Secret-safe logging, file permissions, zeroization where practical | Logs and reports contain no private key |
| `T-12` | Audit ambiguity | Correlation, causation, subject, decision, and monotonic event order | Task history reconstructs issuer and result |

## 6. Phase 0 transport requirements

The authenticated transport prototype MUST:

- authenticate both client and server;
- bind the authenticated certificate identity to the expected machine identity;
- use TLS 1.3 or an equivalently reviewed secure session;
- reject unknown trust roots and identity mismatches;
- place version negotiation inside the protected session;
- cap frame size before allocation;
- close on truncated or malformed frames; and
- avoid writing private key material to benchmark output.

Self-signed development credentials MAY be generated in memory for tests. They MUST be marked development-only and MUST NOT establish a production enrollment design.

## 7. Failure policy

- Authentication failure: close connection, create no peer, expose a rate-limited audit event.
- Integrity failure: close the affected session and preserve no message from it.
- Authorization failure: reject the operation without revealing unnecessary capability details.
- Clock uncertainty: shorten or refuse leases; never extend them silently.
- Runtime-to-adapter disconnect: adapter enters its declared local safe behavior.
- Loss of network: runtime expires presence; the robot applies adapter-specific policy.
- Unknown physical outcome: reconcile or require inspection; never blindly retry.

## 8. Open risks before physical use

- Production enrollment, credential storage, rotation, and revocation are not designed.
- Authorization policy language and administrative roles are not implemented.
- Replay protection beyond TLS session guarantees is incomplete.
- Gateway process isolation has not been validated.
- No independent cryptographic or safety review has occurred.
- Denial-of-service budgets are not yet benchmarked.

No prototype release is approved for unsupervised physical control while these risks remain.

