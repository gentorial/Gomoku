#pragma once
#include "gomoku/search.h"
#include "gomoku/nnue.h"
#include <nlohmann/json.hpp>

namespace gomoku::wire {
using nlohmann::json;
int integer(const json& object, const char* key, int minimum, int maximum);
Move move_from(const json& value, int size);
json move_json(Move move);
Position position_from(const json& value);
json position_json(const Position& position, const json& moves);
json analysis_json(const SearchResult& result, std::string_view evaluator);
SearchLimits limits_from(const json& request);
json about_json(const std::shared_ptr<const NnueModel>& model = {});
std::unique_ptr<Evaluator> evaluator_for(const json& request, const Position& position,
                                       const std::shared_ptr<const NnueModel>& model);
// Synchronous commands shared by native and WASM adapters. Native analyze/stop
// stay in the threaded adapter so cancellation can be received during search.
json dispatch(const json& request, const std::shared_ptr<const NnueModel>& model = {});
}
