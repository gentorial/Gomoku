add_executable(gomoku-bench benchmark.cpp)
target_link_libraries(gomoku-bench PRIVATE gomoku_json)
if(EMSCRIPTEN)
  set_target_properties(gomoku-bench PROPERTIES SUFFIX .mjs)
  target_link_options(gomoku-bench PRIVATE
    --no-entry
    -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createGomokuBench
    -sENVIRONMENT=web,worker,node
    "-sEXPORTED_FUNCTIONS=['_gomoku_bench','_gomoku_bench_load_model','_malloc','_free']"
    "-sEXPORTED_RUNTIME_METHODS=['ccall','HEAPU8']"
    -sALLOW_MEMORY_GROWTH=1 -sMAXIMUM_MEMORY=2147483648
    -sSTACK_SIZE=1048576 -sFILESYSTEM=0)
endif()
