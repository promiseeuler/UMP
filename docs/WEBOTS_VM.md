# Webots ROS 2 Virtual Machine

## Purpose

This is the reference all-in-one virtual setup for seeing UMP execute in a live
Webots 3D scene. The Ubuntu guest runs Webots R2025a, ROS 2 Jazzy, the three UMP
robot drivers, the collaboration planner, and the evidence generator.

The VM demonstration shows protocol-to-simulator integration. It does not claim
that the simplified humanoid walks, the quadruped has a production gait, or the
mobile arm performs validated grasp dynamics.

## Compatibility matrix

| Host | Recommended setup | 3D viewport | Notes |
| --- | --- | --- | --- |
| Ubuntu 24.04 x86-64 | Native installation | Best | Reference and CI-compatible environment |
| Intel macOS | Ubuntu 24.04 x86-64 VM | Good | UTM, VMware, or Parallels can virtualize the guest |
| Apple Silicon macOS | Ubuntu 24.04 x86-64 emulated VM | Slow | UTM/QEMU emulation is required for Linux Webots |
| Windows x86-64 | Ubuntu 24.04 x86-64 VM | Good | Hyper-V, VMware, or VirtualBox |
| Linux x86-64 | Ubuntu 24.04 x86-64 VM | Good | KVM/QEMU is preferred |
| ARM64 Linux VM | Unsupported for this profile | No | Webots R2025a Linux binaries are x86-64 only |
| Headless CI | Ubuntu 24.04 x86-64 plus Xvfb | No | Validates behavior and evidence, not visual quality |

The VM needs at least 4 CPU cores, 8 GB RAM, 40 GB disk, networking, and an
OpenGL 3.3-capable virtual display. Allocate 12 GB RAM and enable 3D acceleration
when the hypervisor supports it.

## Apple Silicon setup with UTM

1. Install UTM:

   ```bash
   brew install --cask utm
   ```

2. Download the Ubuntu 24.04 **amd64 desktop ISO**. Do not select the ARM64 ISO.
3. In UTM, choose **Emulate**, **Linux**, and the downloaded ISO.
4. Select the `x86_64` architecture, 4 CPU cores, 8192 MB RAM, and a 40 GB disk.
5. Enable display hardware acceleration if it works on the host. If the Webots
   viewport is blank or crashes, disable acceleration and use software rendering.
6. Install Ubuntu Desktop, enable the shared clipboard, and reboot the guest.

An x86-64 guest on Apple Silicon is emulated rather than virtualized. Startup and
simulation will be slower, but keeping ROS 2 and Webots inside the same guest
avoids cross-architecture DDS discovery problems.

## Install UMP inside the guest

Open a terminal in the Ubuntu desktop:

```bash
git clone https://github.com/promiseeuler/UMP.git
cd UMP
bash scripts/setup_webots_ros2_vm.sh
```

The script rejects ARM64 and unsupported Ubuntu releases before making changes.
It installs the official ROS 2 apt source, ROS 2 Jazzy Desktop, Webots R2025a,
`webots_ros2_driver`, development dependencies, and then builds the workspace.

## Run the live 3D scene

From an Ubuntu desktop terminal:

```bash
cd ~/UMP
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=74
export ROS2CLI_NO_DAEMON=1
ros2 launch ump_webots_demo warehouse.launch.py
```

Webots opens the warehouse and the coordinator performs three ordered actions:

1. The quadruped inspects the route.
2. The humanoid carries the package to the handoff.
3. The mobile arm places the package on shelf A.

Keep the Webots scene visible while the terminal shows the matching ROS action
state. This makes the relationship between UMP's semantic assignments and robot
motion observable without implying that UMP directly controls motors.

## Validate and retain evidence

For an automated run without the visible viewport:

```bash
cd ~/UMP
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=74
export ROS2CLI_NO_DAEMON=1
xvfb-run -a timeout 240s bash ros2_ws/webots_smoke.sh
ump-ros2-evidence /tmp/ump-ros2-webots-smoke.json \
  --world ros2_ws/src/ump_webots_demo/worlds/ump_warehouse.wbt \
  --revision "$(git rev-parse HEAD)"
```

The command must produce three successful ordered assignments and a validated
evidence file. A screenshot or video is useful for demonstration, but it is not
a replacement for the machine-readable evidence.

## Other compatible setups

- Native Ubuntu x86-64: run the same installer directly, without a VM.
- Intel Mac, Windows, or Linux x86-64: create an Ubuntu 24.04 amd64 VM using the
  host's preferred hypervisor, then run the same guest commands.
- Remote x86-64 workstation: run Webots on that workstation's local display.
  Use remote desktop to view the full desktop; Webots does not support ordinary
  remote X11 redirection as a reliable 3D path.
- CI or servers: use Xvfb for evidence testing. This path intentionally has no
  live 3D viewport.

For the simulator architecture and limitations, continue with
`WEBOTS_ROS2.md`. For physical robots, use `ROBOT_DEPLOYMENT.md` and complete a
supervised hardware pilot before enabling assignments.
