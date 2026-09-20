add_library(gomoku_game STATIC game/position.cpp)
target_include_directories(gomoku_game PUBLIC include)
target_link_libraries(gomoku_game PUBLIC gomoku_options)

add_library(gomoku_engine STATIC eval/handcrafted.cpp eval/nnue.cpp search/search.cpp)
target_link_libraries(gomoku_engine PUBLIC gomoku_game)
