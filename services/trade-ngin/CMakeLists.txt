cmake_minimum_required(VERSION 3.25)

project(trade_ngin LANGUAGES CXX)

enable_testing()

add_library(trade_ngin src/trade_ngin.cpp)
target_include_directories(trade_ngin PUBLIC include)
target_compile_features(trade_ngin PUBLIC cxx_std_20)

add_executable(trade_ngin_smoke tests/smoke_test.cpp)
target_link_libraries(trade_ngin_smoke PRIVATE trade_ngin)

add_test(NAME trade_ngin_smoke COMMAND trade_ngin_smoke)
