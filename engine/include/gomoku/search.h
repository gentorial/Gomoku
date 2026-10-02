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
struct SearchEvent;
// Per-search switches also provide an independent full-window reference path.
// Entries are 16 bytes; the default table is 4 MiB, owned by one search session.
struct SearchOptions {
    bool transpositions = true;
    bool pvs = true;
    std::size_t table_entries = 1 << 18;
    bool vcf = true;
    std::uint64_t vcf_node_limit = 1024;
    int vcf_max_plies = 32;
    // Diagnostics are optional and never inspect evaluator tensors or activations.
    std::function<void(const SearchEvent&)> log = {};
    bool debug_log = false;
};
struct SearchStats {
    std::uint64_t tt_probes = 0, tt_hits = 0, tt_cutoffs = 0;
    std::uint64_t evaluator_pushes = 0, pvs_researches = 0;
    std::uint64_t preferred_cutoffs = 0;
    std::uint64_t vcf_nodes = 0, vcf_wins = 0, vcf_unknown = 0;
    std::uint64_t beta_cutoffs = 0, forced_replies = 0, immediate_wins = 0, double_threats = 0;
    std::uint64_t vcf_calls = 0, vcf_no_win = 0;
    int vcf_elapsed_ms = 0;
};
struct SearchEvent {
    explicit SearchEvent(std::string_view name) : event(name) {}
    std::string_view event;
    std::string_view reason;
    int depth = 0, ply = 0, attempted_depth = 0, completed_depth = 0;
    int score = 0, alpha = 0, beta = 0, elapsed_ms = 0;
    std::uint64_t nodes = 0, node_delta = 0, suppressed = 0;
    int elapsed_delta_ms = 0;
    std::size_t candidate_count = 0;
    std::optional<Move> move;
    std::optional<std::uint64_t> position_hash;
    std::vector<Move> moves;
    std::vector<Move> wins, blocks;
    SearchStats stats;
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
