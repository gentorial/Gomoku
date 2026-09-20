cmake_minimum_required(VERSION 3.24)
get_filename_component(project_root "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
file(GLOB packages "${project_root}/artifacts/gomoku-*.zip")
list(LENGTH packages count)
if(NOT count EQUAL 1)
  message(FATAL_ERROR "Expected exactly one competition ZIP, found ${count}")
endif()
list(GET packages 0 archive)
string(RANDOM LENGTH 12 ALPHABET 0123456789abcdef smoke_id)
set(destination "${project_root}/artifacts/competition-smoke/${smoke_id}")
file(ARCHIVE_EXTRACT INPUT "${archive}" DESTINATION "${destination}")
if(WIN32)
  set(executable "${destination}/pbrain-gomoku64.exe")
else()
  set(executable "${destination}/pbrain-gomoku")
endif()
if(NOT EXISTS "${executable}")
  message(FATAL_ERROR "Competition binary must be at the ZIP root")
endif()
file(WRITE "${destination}/input.txt" "START 15\nINFO timeout_turn 0\nBEGIN\nRESTART\nEND\n")
execute_process(
  COMMAND "${executable}"
  WORKING_DIRECTORY "${destination}"
  INPUT_FILE "${destination}/input.txt"
  OUTPUT_VARIABLE output
  ERROR_VARIABLE errors
  RESULT_VARIABLE result
  TIMEOUT 10)
string(REPLACE "\r\n" "\n" output "${output}")
if(NOT result EQUAL 0 OR NOT output STREQUAL "OK\n7,7\nOK\n")
  message(FATAL_ERROR "Packaged engine failed (${result}): ${output} ${errors}")
endif()
message(STATUS "Packaged engine passed START, BEGIN, RESTART and END")
