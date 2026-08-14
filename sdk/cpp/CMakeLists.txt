cmake_minimum_required(VERSION 3.16)
project(ump_cpp_sdk VERSION 0.1.0 LANGUAGES CXX)

add_library(ump_adapter INTERFACE)
add_library(UMP::adapter ALIAS ump_adapter)
target_compile_features(ump_adapter INTERFACE cxx_std_17)
target_include_directories(
  ump_adapter
  INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)
