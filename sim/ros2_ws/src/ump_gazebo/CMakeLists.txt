cmake_minimum_required(VERSION 3.8)
project(ump_gazebo)

find_package(ament_cmake REQUIRED)
find_package(gz-plugin2 REQUIRED COMPONENTS register)
find_package(gz-sim8 REQUIRED)
find_package(gz-transport13 REQUIRED)

add_library(ump_payload_handoff SHARED src/payload_handoff.cc)
add_library(ump_zone_monitor SHARED src/zone_monitor.cc)
target_compile_features(ump_payload_handoff PUBLIC cxx_std_17)
target_link_libraries(ump_payload_handoff
  gz-plugin2::gz-plugin2
  gz-sim8::gz-sim8
  gz-transport13::gz-transport13
)
target_compile_features(ump_zone_monitor PUBLIC cxx_std_17)
target_link_libraries(ump_zone_monitor
  gz-plugin2::gz-plugin2
  gz-sim8::gz-sim8
  gz-transport13::gz-transport13
)

install(TARGETS ump_payload_handoff ump_zone_monitor LIBRARY DESTINATION lib)
install(DIRECTORY launch models worlds DESTINATION share/${PROJECT_NAME})
ament_package()
