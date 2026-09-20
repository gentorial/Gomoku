#pragma once
#include "gomoku/evaluator.h"
#include <atomic>
#include <chrono>
#include <functional>
#include <optional>
#include <vector>

namespace gomoku {
struct SearchLimits {
    int time_ms = 300;
    int max_depth = 4;
    std::uint64_t max_nodes = 0;
};
struct SearchResult {
    std::optional<Move> best_move;
    int score = 0;
    int depth = 0;
    std::uint64_t nodes = 0;
    int elapsed_ms = 0;
    std::vector<Move> pv;
    std::string_view reason = "completed";
};
using SearchObserver = std::function<void(const SearchResult&)>;

SearchResult search(const Position& root, Evaluator& evaluator, const SearchLimits& limits,
                    const std::atomic_bool& cancelled, const SearchObserver& observer = {});
}  // namespace gomoku
