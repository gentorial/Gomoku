add_library(gomoku_json STATIC codec.cpp)
target_include_directories(gomoku_json PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(gomoku_json PUBLIC gomoku_engine nlohmann_json::nlohmann_json)
