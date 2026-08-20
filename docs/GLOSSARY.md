# UMP Glossary

- **Adapter:** Software that translates between UMP and a machine's native controller or API.
- **Authority:** Permission for a subject to perform a scoped action.
- **Capability:** A versioned declaration of something a machine can observe or do.
- **Command:** A bounded request for an immediate operation within existing authority.
- **Coordinator:** An optional participant that assigns or decomposes work.
- **Gateway:** A UMP runtime outside a locked-down machine that represents it through an approved interface.
- **Handoff:** A coordinated transfer of responsibility or ownership between machines.
- **Lease:** A time-bounded grant of authority or resource access.
- **Machine:** An autonomous or controlled physical system represented by one UMP identity.
- **Peer:** Another authenticated UMP runtime in the current communication context.
- **Presence:** Time-limited evidence that a peer and session are reachable.
- **Profile:** A named set of required UMP features and conformance behavior.
- **Resource:** A uniquely identified asset, space, tool, or capacity that can be coordinated.
- **Runtime:** The `umpd` implementation of protocol sessions and state machines.
- **Session:** One runtime boot or connection epoch, distinct from stable machine identity.
- **Task:** A lifecycle-managed request for a capability outcome.

