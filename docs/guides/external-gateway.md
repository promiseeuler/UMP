# External Gateway

An external gateway lets a locked-down machine join UMP when its controller exposes an approved network API but cannot host `umpd`. No additional hardware is required when an existing nearby Linux computer can host the gateway; otherwise that computer is the fallback deployment location.

The gateway does not unlock unsupported robot functions. A machine with no approved command interface can participate only in read-only observation mode.

## Identity model

The represented robot keeps its own stable `ump:machine:` identity. The host has a separate stable `ump:gateway:` identifier. The authenticated machine descriptor advertises `DEPLOYMENT_MODE_GATEWAY_PROXY` and a proxy association containing both identifiers, the controller-interface profile, and read-only status.

One gateway configuration represents exactly one machine. `ump_gateway.py` refuses to run when its configured machine, local `umpd` identity, and descriptor association disagree.

## Configure

1. Install and initialize `umpd` using the native package or container on the gateway host.
2. Configure the vendor HTTP bridge using [the controller bridge guide](vendor-http-bridge.md).
3. Start from `gateway/config.example.json` and set the represented machine, gateway ID, interface profile, runtime config, bridge config, and status path.
4. Stop `umpd`, then bind the association into its descriptor configuration:

```sh
python3 gateway/ump_gateway.py --config /etc/ump/gateway.json --configure-runtime
```

5. Start `umpd`, followed by the gateway supervisor:

```sh
python3 gateway/ump_gateway.py --config /etc/ump/gateway.json
```

Run `umpd` and the gateway under separate unprivileged service accounts or separate rootless containers. Grant the gateway only access to the represented runtime's `adapter.sock`, controller network destination, read-only configuration, and its status directory. Do not mount runtime credentials into the bridge container; only `umpd` needs the UMP private key.

## Disconnect and restart behavior

Before claiming command work, the supervisor verifies local runtime identity and probes the configured controller health endpoint. A failed probe publishes degraded UMP state, writes `controller_disconnected`, and does not call `next_task`. Recovery becomes `connected` only after a successful probe.

The atomic JSON status file always includes gateway ID, represented machine, controller interface, proxy flag, read-only flag, connectivity, mode, fault code, revision, and observation time. Revisions advance across gateway restart and from the authoritative `umpd` state revision.

Read-only mode requires an observation endpoint. It maps bounded operational, safety, and health state into UMP and never claims tasks. Unknown or invalid controller state fails closed as degraded/disconnected.

Controller loss during an already executing physical effect follows the adapter safety contract: the task becomes failed or uncertain based on native evidence, local controller safety remains authoritative, and the gateway never performs a blind retry.

## Verify

```sh
make gateway-test
make sim-s5
```

S5 runs the same six-task QUIC mission with the arm behind an onboard SDK bridge and then behind the external gateway. It proves equivalent terminal task and handoff ownership behavior, explicit proxy visibility, controller disconnect/recovery, and gateway restart continuity.
