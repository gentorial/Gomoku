find_package(Threads REQUIRED)
add_executable(gomoku-worker main.cpp)
target_link_libraries(gomoku-worker PRIVATE gomoku_json Threads::Threads)
