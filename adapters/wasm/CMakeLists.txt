if(NOT EMSCRIPTEN)
  message(FATAL_ERROR "The WASM adapter requires Emscripten; run pnpm wasm:build")
endif()
add_executable(gomoku-wasm main.cpp)
target_link_libraries(gomoku-wasm PRIVATE gomoku_json)
set_target_properties(gomoku-wasm PROPERTIES
  OUTPUT_NAME gomoku-engine SUFFIX .mjs
  RUNTIME_OUTPUT_DIRECTORY "${PROJECT_SOURCE_DIR}/packages/engine-wasm/generated")
foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
  set_target_properties(gomoku-wasm PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY_${config} "${PROJECT_SOURCE_DIR}/packages/engine-wasm/generated")
endforeach()
target_link_options(gomoku-wasm PRIVATE
  --no-entry
  -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createGomokuModule
  -sENVIRONMENT=web,worker,node
  "-sEXPORTED_FUNCTIONS=['_gomoku_request','_gomoku_load_model','_gomoku_nnue_predict','_malloc','_free']"
  "-sEXPORTED_RUNTIME_METHODS=['ccall','HEAPU8']"
  -sALLOW_MEMORY_GROWTH=1 -sMAXIMUM_MEMORY=2147483648
  -sSTACK_SIZE=1048576 -sFILESYSTEM=0)
