#pragma once
#include "gomoku/evaluator.h"
#include <atomic>
#include <chrono>
#include <functional>
#include <optional>
#include <string_view>
#include <vector>

namespace gomoku {
struct SearchLimits {
    int time_ms = 300;
    // Searches end on time, nodes or cancellation. A smaller depth is only for
    // fixed-depth tests and profiling; 64 is the iterative-deepening ceiling.
    int max_depth = 64;
    std::uint64_t max_nodes = 0;
};
// Per-search switches also provide an independent full-window reference path.
// Entries are 16 bytes; the default table is 4 MiB, owned by one search session.
struct SearchOptions {
    bool transpositions = true;
    bool pvs = true;
    std::size_t table_entries = 1 << 18;
    bool vcf = true;
    std::uint64_t vcf_node_limit = 1024;
    int vcf_max_plies = 32;
    // Against an opponent's open three, search only own fours and moves that
    // remove every point where the opponent would get two winning points.
    bool threat_filter = true;
    // Late quiet moves (in policy order) first get a reduced null-window search.
    bool lmr = true;
    int lmr_min_depth = 3;
    // Tuned 2026-10-10 by SPRT arenas against the earlier 3 / 0.5 / 2.0 (docs/tactical-search.md).
    int lmr_min_moves = 2;
    double lmr_base = 0.25;
    double lmr_divisor = 1.0;
};
// Applies one "name=value" override (as passed to the worker's --search flag).
// Names are the SearchOptions fields; booleans are 0/1. Throws on unknown names
// or out-of-range values.
void set_search_option(SearchOptions& options, std::string_view assignment);
struct SearchStats {
    std::uint64_t tt_probes = 0, tt_hits = 0, tt_cutoffs = 0;
    std::uint64_t evaluator_pushes = 0, pvs_researches = 0;
    std::uint64_t preferred_cutoffs = 0;
    std::uint64_t vcf_nodes = 0, vcf_wins = 0, vcf_unknown = 0;
    std::uint64_t threatened_nodes = 0, threat_losses = 0;
    std::uint64_t lmr_reductions = 0, lmr_researches = 0;
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
