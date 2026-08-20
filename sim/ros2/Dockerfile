FROM ros:jazzy-ros-base-noble@sha256:da725acf8b0f9f30c683e33ffbdcd6482d077af96d6fdc7688c5f4f280b7d923

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY sim/ros2/rosdep.lock /tmp/ump-rosdep.lock
RUN apt-get update \
    && xargs -a /tmp/ump-rosdep.lock apt-get install -y --no-install-recommends \
    && dpkg-query -W -f='${Package}=${Version}\n' $(cat /tmp/ump-rosdep.lock) \
       | sort > /opt/ump-ros-packages.lock \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspaces/ump_ros2
COPY sim/ros2_ws/src/ump_interfaces src/ump_interfaces
RUN source /opt/ros/jazzy/setup.bash \
    && colcon build --merge-install --packages-select ump_interfaces \
       --event-handlers console_direct+

COPY sim/ros2_ws/src/ump_gazebo src/ump_gazebo
RUN source /opt/ros/jazzy/setup.bash \
    && source install/setup.bash \
    && colcon build --merge-install \
       --packages-select ump_gazebo \
       --event-handlers console_direct+

COPY sim/ros2_ws/src/ump_ros2_adapter src/ump_ros2_adapter
RUN source /opt/ros/jazzy/setup.bash \
    && source install/setup.bash \
    && colcon build --merge-install \
       --packages-select ump_ros2_adapter \
       --event-handlers console_direct+

COPY sim/ros2/entrypoint.sh /ump-entrypoint.sh
COPY sim/ros2/smoke.sh /workspaces/ump_ros2/sim-smoke.sh
ENTRYPOINT ["/ump-entrypoint.sh"]
CMD ["bash"]
