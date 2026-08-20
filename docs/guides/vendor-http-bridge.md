# Vendor HTTP Controller Bridge

The HTTP bridge lets a locked-down or vendor-managed robot participate in UMP through an approved controller API. It runs beside `umpd`; it does not require modifying controller firmware or adding dedicated hardware when the robot already provides a reachable API and somewhere approved to run the bridge.

## Boundary

```text
UMP peer -> authenticated ump transport -> umpd -> local Unix API
  -> allowlisted HTTP bridge -> vendor controller API -> robot controller
```

Task data selects only the input to a preconfigured capability. It cannot select a URL, HTTP path, credential, or timeout. The bridge accepts JSON task input, sends one bounded request, polls UMP cancellation state, and maps the controller's terminal response back to the durable task timeline.

This bridge is not a real-time motion bus, emergency-stop channel, or safety controller. Keep servo loops, certified interlocks, limits, collision handling, and emergency functions inside the native robot controller.

## Configure

Start from `bridge/http/config.example.json`. Each capability has an allowlisted execution path, optional cancellation path, timeout, and input limit.

- Use HTTPS for every production controller.
- Configure a trusted CA with `ca_file` for private controller PKI.
- Use `token_env`, mTLS `client_certificate` plus `client_key`, or both. Secrets are not accepted inline.
- HTTP is accepted only on loopback when `allow_insecure_loopback` is explicitly true.
- Set `read_only` during commissioning to prove that the process can connect without claiming tasks.
- Map an interruptible UMP capability only when the native controller provides a real cancellation operation.

The process rejects redirects, oversized responses, expired tasks, unsupported media types, unmapped capabilities, traversal paths, and malformed controller responses. Its controller timeout is bounded by the task deadline.

## Run the example

For a local development exercise, change the example config URL to `http://127.0.0.1:8080`, remove certificate fields, and set `allow_insecure_loopback` to `true`.

```sh
export UMP_EXAMPLE_CONTROLLER_TOKEN=development-only
export UMP_VENDOR_CONTROLLER_TOKEN="$UMP_EXAMPLE_CONTROLLER_TOKEN"
python3 bridge/http/example_controller.py --port 8080
python3 bridge/http/ump_http_bridge.py --config bridge/http/config.example.json
```

The example controller is deliberately small and loopback-only. It demonstrates the request contract, not production controller behavior.

## Controller contract

Execution receives:

```json
{
  "task_id": "...",
  "capability": "org.example.robot.move",
  "input": {},
  "deadline_ms": 0,
  "correlation_id": "..."
}
```

A successful controller response is `{"status":"succeeded","output":{}}`. A failure is `{"status":"failed","error_code":"vendor.reason","retryable":false}`. Cancellation receives `{"task_id":"..."}`. Controller implementations must deduplicate by `task_id` and reconcile uncertain outcomes instead of blindly repeating physical action.

## Industrial interfaces

For OPC UA, Modbus TCP, EtherNet/IP, PROFINET, or EtherCAT systems, terminate UMP at an approved controller service or vendor SDK adapter. That adapter should invoke validated PLC function blocks or controller jobs and derive completion from controller state. Do not translate remote UMP tasks directly into cyclic fieldbus writes or raw actuator setpoints.

Before a physical deployment, complete [the adapter safety contract](../safety/ADAPTER_SAFETY_CONTRACT.md), test every failure mode, and retain a local operator and emergency stop.
