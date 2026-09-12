# The Construct Cloud Test

This bundle runs a recorded UMP mixed-fleet test in a browser-hosted ROS
workspace. It is designed for a public The Construct ROS project and keeps the
UMP protocol path identical to the local software lab.

## What the test proves

The test creates three independently identified UMP participants: a mobile
robot, an inspection robot, and a manipulator. They publish manifests and
semantic state, receive an authority-validated collaborative plan, complete
dependency-ordered assignments, and publish outcomes. An `InspectorRecorder`
captures the canonical envelopes from the same message bus.

When a compatible Gazebo `gz` command is available, the test launches
`gazebo/warehouse.sdf` and uses Gazebo pose services as the native execution
backend. Otherwise it runs the deterministic headless controller backend. The
script prints the selected backend, so recorded material must not imply Gazebo
was used when it was not.

## Free-plan setup

1. Create a free The Construct account and a public ROS project.
2. Open the project terminal.
3. Clone the public UMP repository and enter it:

```sh
git clone https://github.com/promiseeuler/UMP.git
cd UMP
```

4. Run the test:

```sh
bash cloud/the_construct/run_test.sh
```

## Record the paced visual demonstration

Start Gazebo from the Construct terminal, then open its **Simulation** panel:

```sh
DISPLAY=:2 gz sim -r ~/UMP/gazebo/warehouse.sdf
```

In a second terminal, execute the same UMP scenario against that live world:

```sh
python3 -m ump.gazebo_lab \
  --world ~/UMP/gazebo/warehouse.sdf \
  --reuse-server \
  --demo-duration 7 \
  --workspace /tmp/ump-visual-runtime \
  --inspector-database /tmp/ump-visual-inspector.sqlite3
```

The presentation delay makes the AMR transport, quadruped inspection, and
payload placement visible while preserving the normal UMP authority and
coordinator path. The models are representative self-contained SDF assets, not
manufacturer-certified digital twins or physical hardware.

The free plan may limit runtime, storage, and available ROS/Gazebo images. The
headless fallback still proves UMP collaboration and recording, but it is not an
external physics-simulator result.

The runner creates `.ump-cloud-venv` automatically because The Construct uses
Ubuntu's externally-managed Python policy. It does not modify the system Python
installation. If the workspace image does not include `python3-venv`, it uses
the Construct user's site directory with the explicit Ubuntu override instead.

## Evidence artifacts

Each run writes to a new UTC timestamped directory under `.ump-cloud-runs/`:

```text
scenario.json
inspector.sqlite3
content/evidence.json
content/timeline.csv
content/pitch-summary.md
```

- `scenario.json` records the scenario result and step outcomes.
- `inspector.sqlite3` is the append-only UMP protocol record.
- `evidence.json` contains robot IDs, message counts, lab events, checksums, and
  the simulation claim boundary.
- `timeline.csv` is suitable for charts, editing notes, and event animation.
- `pitch-summary.md` is a concise factual narrative generated from the records.

The exporter reads the inspector database in SQLite read-only mode and refuses
to overwrite an existing content directory.

## View the inspector

The Construct's port and TLS controls vary by workspace. Prefer its authenticated
private port forwarding. On a private workspace interface, start:

```sh
ump-inspector \
  --database .ump-cloud-runs/RUN_ID/inspector.sqlite3 \
  --host 127.0.0.1 --port 8765
```

For a non-loopback bind, UMP requires `--allow-remote`, a token of at least 32
bytes, and TLS certificate/key files. Never expose the inspector directly as an
unauthenticated public port.

## Use in content and pitches

Safe statement:

> In a recorded software simulation, three heterogeneous robot roles shared
> semantic state through UMP and completed an authority-validated collaborative
> workflow. Every protocol event and assignment outcome was retained for review.

Do not claim that this test proves universal robot compatibility, physical
safety, real-time performance, manufacturer certification, or production
hardware readiness. State whether the run used `gazebo` or `headless`, and retain
the corresponding `evidence.json` with any published result.
