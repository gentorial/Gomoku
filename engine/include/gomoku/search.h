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
// Per-search switches also provide an independent full-window reference path.
// Entries are 16 bytes; the default table is 4 MiB, owned by one search session.
struct SearchOptions {
    bool transpositions = true;
    bool pvs = true;
    std::size_t table_entries = 1 << 18;
};
struct SearchStats {
    std::uint64_t tt_probes = 0, tt_hits = 0, tt_cutoffs = 0;
    std::uint64_t evaluator_pushes = 0, pvs_researches = 0;
    std::uint64_t preferred_cutoffs = 0;
};
struct SearchResult {
    std::optional<Move> best_move;
    int score = 0;
    int depth = 0;
    std::uint64_t nodes = 0;
    int elapsed_ms = 0;
    std::vector<Move> pv;
    std::string_view reason = "completed";
    SearchStats stats;
};
using SearchObserver = std::function<void(const SearchResult&)>;

SearchResult search(const Position& root, Evaluator& evaluator, const SearchLimits& limits,
                    const std::atomic_bool& cancelled, const SearchObserver& observer = {},
                    const SearchOptions& options = {});
}  // namespace gomoku
