FROM ump-ros2:phase4

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN source /opt/ros/jazzy/setup.bash \
    && colcon --log-base /tmp/ump-ros-log build --merge-install \
       --base-paths /workspaces/ump_ros2/src \
       --build-base /tmp/ump-ros-build \
       --install-base /opt/ump/ros/jazzy \
       --packages-select ump_interfaces ump_gazebo ump_ros2_adapter \
       --event-handlers console_direct+
COPY packaging/ros2/build-deb.sh /usr/local/bin/build-ump-ros2-deb
ENTRYPOINT ["/usr/local/bin/build-ump-ros2-deb"]
