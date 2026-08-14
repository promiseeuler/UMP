# UMP C++ adapter preview

This header-only preview defines the ROS 2-facing boundaries needed before Phase 4: frame resolution, reservation-loss notification, and handoff observation. It intentionally does not perform motion control or bypass native safety checks.

```cmake
add_subdirectory(path/to/ump/sdk/cpp)
target_link_libraries(my_robot_adapter PRIVATE UMP::adapter)
```

Protocol Buffer C++ generation and the ROS 2 transport adapter arrive in Phase 4. The preview requires C++17 and has no runtime dependency.
