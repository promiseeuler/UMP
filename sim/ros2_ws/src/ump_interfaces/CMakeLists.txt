cmake_minimum_required(VERSION 3.8)
project(ump_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/ExecuteCapability.action"
  "msg/AdapterState.msg"
  "msg/LocalizationUncertainty.msg"
  "msg/SafetyEvent.msg"
  "msg/SpatialSample.msg"
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
