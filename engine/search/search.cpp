#include "gomoku/search.h"
#include "gomoku/profile.h"
#include "gomoku/vcf.h"
#include <algorithm>
#include <bit>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace gomoku {
namespace {
constexpr int mate = 100000;
constexpr int infinity = mate + 1;
struct Interrupted { std::string_view reason; };
using Clock = std::chrono::steady_clock;
enum class Bound : std::uint8_t { empty, exact, lower, upper };
struct Entry {
    std::uint64_t key = 0;
    std::int32_t score = 0;
    std::int16_t move = -1;
    std::uint8_t depth = 0;
    Bound bound = Bound::empty;
};
static_assert(sizeof(Entry) == 16);

// A mate distance is relative to the root in search, but relative to this node
// in the table. Ordinary NNUE/handcrafted scores are below the mate range.
int to_table(int score, int ply) {
    return score > mate - 1000 ? score + ply : score < -mate + 1000 ? score - ply : score;
}
int from_table(int score, int ply) {
    return score > mate - 1000 ? score - ply : score < -mate + 1000 ? score + ply : score;
}
struct Played {
    Position& position;
    Threats* threats;
    Move move;
    Played(Position& p, Move m, Threats* t = nullptr) : position(p), threats(t), move(m) {
        const auto color = p.turn(); position.play(move);
        if (threats) threats->play(move, color);
    }
    ~Played() { if (threats) threats->undo(move); position.undo(); }
    Played(const Played&) = delete;
};
struct Accumulated {
    Evaluator& evaluator;
    Accumulated(Evaluator& e, const Position& p, Move move) : evaluator(e) { evaluator.push(p, move); }
    ~Accumulated() { evaluator.pop(); }
    Accumulated(const Accumulated&) = delete;
};

struct Search {
    Evaluator& evaluator;
    const SearchLimits& limits;
    const std::atomic_bool& cancelled;
    const SearchOptions& options;
    Clock::time_point start = Clock::now();
    std::uint64_t nodes = 0;
    SearchStats stats;
    std::vector<Entry> table;
    std::optional<Threats> threats;
    int attempted_depth = 0, completed_depth = 0;
    std::uint64_t detail_count = 0, suppressed = 0;

    bool details(int ply = 0) {
        if (!options.log || !options.debug_log || ply > 2) return false;
        if (detail_count >= 500) { ++suppressed; return false; }
        return true;
    }
    void log(SearchEvent event, bool detail = false) noexcept {
        if (!options.log) return;
        if (detail && detail_count++ >= 500) { ++suppressed; return; }
        event.attempted_depth = attempted_depth;
        event.completed_depth = completed_depth;
        event.nodes = nodes; event.elapsed_ms = elapsed(); event.stats = stats;
        event.suppressed = suppressed;
        try { options.log(event); } catch (...) { /* Logging must not break make/unmake. */ }
    }

    Search(Evaluator& e, const SearchLimits& l, const std::atomic_bool& c, const SearchOptions& o, const Position& root)
        : evaluator(e), limits(l), cancelled(c), options(o),
          table(o.transpositions ? o.table_entries : 0) {
        if (o.vcf) threats.emplace(root);
    }
    Threats* tactics() { return threats ? &*threats : nullptr; }
    int elapsed() const {
        return static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count());
    }
    void check() const {
        GOMOKU_SCOPE(checks);
        if (cancelled.load(std::memory_order_relaxed)) throw Interrupted{"cancelled"};
        if (elapsed() >= limits.time_ms) throw Interrupted{"time_limit"};
        if (limits.max_nodes != 0 && nodes >= limits.max_nodes) throw Interrupted{"node_limit"};
    }
    const Entry* probe(std::uint64_t key) {
        if (table.empty()) return nullptr;
        ++stats.tt_probes;
        const auto& entry = table[key & (table.size()-1)];
        if (entry.bound == Bound::empty || entry.key != key) return nullptr;
        ++stats.tt_hits;
        return &entry;
    }
    void store(std::uint64_t key, int depth, int score, Bound bound, std::optional<Move> move, int ply) {
        if (table.empty()) return;
        auto& entry = table[key & (table.size()-1)];
        if (entry.bound != Bound::empty && entry.depth > depth) return;
        entry = {key, to_table(score, ply), static_cast<std::int16_t>(move ? move->y*20+move->x : -1),
            static_cast<std::uint8_t>(depth), bound};
    }
    std::vector<Move> ordered(const Position& position, int ply) {
        if (threats) {
            const auto side = position.turn();
            const bool win = threats->winning_count(side) != 0;
            if (win || threats->winning_count(opposite(side))) {
                auto moves = threats->winning_moves(win ? side : opposite(side));
                if (details(ply)) {
                    SearchEvent event{"search.candidates"}; event.ply = ply; event.position_hash = position.hash();
                    event.reason = win ? "immediate_win" : "forced_defense";
                    event.candidate_count = moves.size(); event.moves = moves;
                    (win ? event.wins : event.blocks) = moves; log(event, true);
                }
                return moves;
            }
        }
        struct Ranked { Move move; int priority; double policy; int heuristic; std::size_t ordinal; };
        std::vector<Ranked> ranked;
        const auto candidates = position.candidates();
        ranked.reserve(candidates.size());
        check();
        const auto policy = evaluator.move_scores(position, candidates);
        std::size_t tactical = 0;
        {
            GOMOKU_SCOPE(ranking);
            for (std::size_t i = 0; i < candidates.size(); ++i) {
                const Move move = candidates[i];
                check();
                int score = 0;
                const int priority = position.wins(move, position.turn()) ? 3 :
                    position.wins(move, opposite(position.turn())) ? 2 : 0;
                tactical += priority != 0;
                for (int dy = -2; dy <= 2; ++dy) {
                    for (int dx = -2; dx <= 2; ++dx) {
                        Move p{move.x + dx, move.y + dy};
                        if (position.contains(p) && position.at(p) != Color::empty)
                            score += 10 - std::abs(dx) - std::abs(dy);
                    }
                }
                score -= std::abs(move.x - position.size() / 2) + std::abs(move.y - position.size() / 2);
                ranked.push_back({move, priority, policy.empty() ? 0 : policy.at(i), score, i});
            }
        }
        const auto count = std::min(ranked.size(), std::max(std::size_t(16), tactical));
        {
            GOMOKU_SCOPE(sorting);
            std::partial_sort(ranked.begin(), ranked.begin()+count, ranked.end(), [](const auto& a, const auto& b) {
                if (a.priority != b.priority) return a.priority > b.priority;
                if (a.policy != b.policy) return a.policy > b.policy;
                if (a.heuristic != b.heuristic) return a.heuristic > b.heuristic;
                return a.ordinal < b.ordinal;
            });
        }
        // Preserve the original selective candidate set: top 16 plus every
        // immediate win/block. TT/PV ordering must never displace a quiet move
        // from this set, otherwise a TT bound could describe a different tree.
        std::vector<Move> moves;
        moves.reserve(count);
        for (std::size_t i = 0; i < count; ++i) moves.push_back(ranked[i].move);
        if (details(ply)) {
            SearchEvent event{"search.candidates"}; event.ply = ply; event.position_hash = position.hash();
            event.candidate_count = candidates.size(); event.moves = moves;
            for (std::size_t i = 0; i < count; ++i) {
                if (ranked[i].priority == 3) event.wins.push_back(ranked[i].move);
                if (ranked[i].priority == 2) event.blocks.push_back(ranked[i].move);
            }
            event.reason = "top16_and_tactical"; log(event, true);
        }
        return moves;
    }
    struct Picker {
        Search& search;
        const Position& position;
        std::optional<Move> preferred;
        bool tried_preferred = false, generated = false;
        std::size_t index = 0;
        std::vector<Move> moves;
        int ply;
        Picker(Search& s, const Position& p, std::optional<Move> first, int level = 0)
            : search(s), position(p), preferred(first), ply(level) {}
        std::optional<Move> next() {
            // This is a previously searched move at the identical position,
            // so it belongs to the selected set even before policy is computed.
            if (!tried_preferred) {
                tried_preferred = true;
                if (preferred) {
                    if (search.details(ply)) { SearchEvent e{"search.preferred_move"}; e.ply = ply; e.position_hash = position.hash(); e.move = preferred; e.reason = ply == 0 ? "previous_iteration" : "transposition_table"; search.log(e, true); }
                    return preferred;
                }
            }
            if (!generated) { moves = search.ordered(position, ply); generated = true; }
            while (index < moves.size()) {
                const auto move = moves[index++];
                if (move != preferred) return move;
            }
            return {};
        }
    };
    int negamax(Position& position, int depth, int alpha, int beta, int ply,
                Move last_move, std::vector<Move>& pv) {
        check();
        ++nodes;
        const auto node_hash = position.hash();
        pv.clear();
        if (position.winner() != Color::empty) return -mate + ply;
        if (position.full()) return 0;
        bool forced = false;
        if (threats) {
            const auto side = position.turn(), other = opposite(side);
            if (threats->winning_count(side)) {
                ++stats.immediate_wins;
                pv = {threats->winning_moves(side).front()};
                if (details(ply)) { SearchEvent e{"search.tactic"}; e.ply = ply; e.position_hash = node_hash; e.reason = "immediate_win"; e.moves = pv; log(e, true); }
                return mate - ply - 1;
            }
            const int danger = threats->winning_count(other);
            if (danger >= 2) {
                ++stats.double_threats;
                const auto points = threats->winning_moves(other);
                pv = {points[0], points[1]};
                if (details(ply)) { SearchEvent e{"search.tactic"}; e.ply = ply; e.position_hash = node_hash; e.reason = "double_threat_loss"; e.moves = pv; log(e, true); }
                return -mate + ply + 2;
            }
            forced = danger == 1;
            if (forced) {
                ++stats.forced_replies;
                if (details(ply)) { SearchEvent e{"search.tactic"}; e.ply = ply; e.position_hash = node_hash; e.depth = depth; e.reason = depth == 0 ? "leaf_defense_extension" : "forced_defense"; e.moves = threats->winning_moves(other); log(e, true); }
            }
            if (depth == 0 && !forced && threats->four_count(side)) {
                const auto vcf_start = Clock::now();
                const auto vcf_nodes_before = stats.vcf_nodes;
                ++stats.vcf_calls;
                const auto proof = solve_vcf(position, *threats,
                    {options.vcf_max_plies, options.vcf_node_limit}, [&] {
                        check(); ++nodes; ++stats.vcf_nodes;
                    });
                const int vcf_elapsed = static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-vcf_start).count());
                stats.vcf_elapsed_ms += vcf_elapsed;
                stats.vcf_wins += proof.status == VcfStatus::win;
                stats.vcf_unknown += proof.status == VcfStatus::unknown;
                stats.vcf_no_win += proof.status == VcfStatus::no_win;
                if (details(ply)) {
                    SearchEvent e{"search.vcf"}; e.ply = ply; e.position_hash = node_hash; e.moves = proof.pv;
                    e.reason = proof.status == VcfStatus::win ? "win" : proof.status == VcfStatus::unknown ? "unknown" : "no_win";
                    e.node_delta = stats.vcf_nodes - vcf_nodes_before;
                    e.elapsed_delta_ms = vcf_elapsed;
                    log(e, true);
                }
                if (proof.status == VcfStatus::win) {
                    pv = proof.pv;
                    return mate - ply - static_cast<int>(pv.size());
                }
            }
        }
        std::optional<Move> preferred;
        if (const auto* entry = probe(position.hash())) {
            if (entry->move >= 0) {
                const Move move{entry->move%20, entry->move/20};
                if (position.legal(move)) preferred = move;
            }
            const int value = from_table(entry->score, ply);
            // Forced replies can extend a nominal leaf. Preserve their PV in
            // full windows; quiet leaves and null windows can return bounds.
            if (entry->depth >= depth && ((depth == 0 && !forced) || beta-alpha == 1) &&
                (entry->bound == Bound::exact || (entry->bound == Bound::lower && value >= beta) ||
                 (entry->bound == Bound::upper && value <= alpha))) {
                ++stats.tt_cutoffs;
                if (details(ply)) { SearchEvent e{"search.tt_cutoff"}; e.ply = ply; e.position_hash = node_hash; e.depth = depth; e.score = value; e.alpha = alpha; e.beta = beta; e.move = preferred; e.reason = entry->bound == Bound::exact ? "exact" : entry->bound == Bound::lower ? "lower" : "upper"; log(e, true); }
                return value;
            }
        }
        // Parent accumulators remain active until this point. Terminal states
        // and TT cutoffs do not pay for a convolution update or its undo frame.
        Accumulated accumulated(evaluator, position, last_move);
        ++stats.evaluator_pushes;
        if (depth == 0 && !forced) {
            const int value = evaluator.evaluate(position);
            store(position.hash(), 0, value, Bound::exact, {}, ply);
            return value;
        }
        const int original_alpha = alpha;
        int best = -infinity;
        std::optional<Move> best_move;
        Picker picker(*this, position, preferred, ply);
        int searched = 0;
        while (const auto move = picker.next()) {
            std::vector<Move> child;
            int value;
            {
                Played played(position, *move, tactics());
                const int child_depth = std::max(depth-1, 0);
                if (options.pvs && searched > 0) {
                    value = -negamax(position, child_depth, -alpha-1, -alpha, ply+1, *move, child);
                    if (value > alpha && value < beta) {
                        ++stats.pvs_researches;
                        if (details(ply)) { SearchEvent e{"search.pvs_research"}; e.ply = ply; e.position_hash = node_hash; e.depth = depth; e.move = move; e.score = value; e.alpha = alpha; e.beta = beta; log(e, true); }
                        value = -negamax(position, child_depth, -beta, -alpha, ply+1, *move, child);
                    }
                } else value = -negamax(position, child_depth, -beta, -alpha, ply+1, *move, child);
            }
            ++searched;
            if (value > best) {
                best = value; best_move = move;
                pv = {*move};
                pv.insert(pv.end(), child.begin(), child.end());
            }
            alpha = std::max(alpha, value);
            if (alpha >= beta) {
                ++stats.beta_cutoffs;
                if (!picker.generated) ++stats.preferred_cutoffs;
                if (details(ply)) { SearchEvent e{"search.beta_cutoff"}; e.ply = ply; e.position_hash = node_hash; e.depth = depth; e.move = move; e.score = value; e.alpha = original_alpha; e.beta = beta; e.reason = !picker.generated ? "preferred_move" : "ordered_move"; log(e, true); }
                break;
            }
        }
        store(position.hash(), depth, best, best >= beta ? Bound::lower :
            best <= original_alpha ? Bound::upper : Bound::exact, best_move, ply);
        return best;
    }
};
}

SearchResult search(const Position& root, Evaluator& evaluator, const SearchLimits& limits,
                    const std::atomic_bool& cancelled, const SearchObserver& observer,
                    const SearchOptions& options) {
    GOMOKU_SCOPE(search);
    if (limits.time_ms < 0 || limits.max_depth < 1 || limits.max_depth > 64)
        throw std::invalid_argument("Invalid search limits");
    if (options.transpositions && (!std::has_single_bit(options.table_entries) || options.table_entries > (1 << 18)))
        throw std::invalid_argument("TT entries must be a power of two up to 262144");
    if (options.vcf_max_plies < 1 || options.vcf_max_plies > 400)
        throw std::invalid_argument("Invalid VCF depth");
    Search searcher(evaluator, limits, cancelled, options, root);
    SearchResult result;
    searcher.log(SearchEvent{"search.start"});
    evaluator.reset(root);
    if (root.terminal()) {
        result.reason = "terminal";
        result.score = root.winner() == Color::empty ? 0 : -mate;
        SearchEvent e{"search.end"}; e.reason = "terminal"; e.score = result.score; searcher.log(e);
        return result;
    }
    const auto fallback = root.candidates();
    result.best_move = fallback.front();
    {
        GOMOKU_SCOPE(ranking);
        // Even an immediate timeout returns a legal move; prefer an immediate win/block.
        for (auto move : fallback) {
            if (root.wins(move, opposite(root.turn()))) result.best_move = move;
        }
        for (auto move : fallback) {
            if (root.wins(move, root.turn())) { result.best_move = move; break; }
        }
    }
    result.pv = {*result.best_move};
    result.score = evaluator.evaluate(root);
    if (searcher.details()) { SearchEvent e{"search.fallback"}; e.move = result.best_move; e.score = result.score; searcher.log(e, true); }
    Position position = root;
    std::string_view stop_reason = "max_depth";
    try {
        for (int depth = 1; depth <= limits.max_depth; ++depth) {
            searcher.attempted_depth = depth;
            const auto iteration_nodes = searcher.nodes;
            const auto iteration_elapsed = searcher.elapsed();
            int best = -infinity, alpha = -infinity;
            std::vector<Move> best_pv;
            Search::Picker picker(searcher, position, result.depth > 0 ? result.best_move : std::nullopt);
            int searched = 0;
            while (const auto move = picker.next()) {
                searcher.check();
                const auto move_nodes = searcher.nodes;
                const auto move_elapsed = searcher.elapsed();
                const int window_alpha = alpha;
                bool researched = false;
                std::vector<Move> child;
                int value;
                {
                    Played played(position, *move, searcher.tactics());
                    if (options.pvs && searched > 0) {
                        value = -searcher.negamax(position, depth-1, -alpha-1, -alpha, 1, *move, child);
                        if (value > alpha) {
                            ++searcher.stats.pvs_researches;
                            researched = true;
                            if (searcher.details()) { SearchEvent e{"search.pvs_research"}; e.depth = depth; e.move = move; e.score = value; e.alpha = alpha; e.beta = infinity; searcher.log(e, true); }
                            value = -searcher.negamax(position, depth-1, -infinity, -alpha, 1, *move, child);
                        }
                    } else value = -searcher.negamax(position, depth-1, -infinity, -alpha, 1, *move, child);
                }
                ++searched;
                if (searcher.details()) {
                    SearchEvent e{"search.root_move"}; e.depth = depth; e.move = move; e.score = value;
                    e.alpha = window_alpha;
                    e.beta = options.pvs && searched > 1 && !researched ? window_alpha + 1 : infinity;
                    e.reason = researched ? "pvs_research" : options.pvs && searched > 1 ? "pvs_bound" : "full_window";
                    e.node_delta = searcher.nodes-move_nodes; e.elapsed_delta_ms = searcher.elapsed()-move_elapsed;
                    e.moves = child; searcher.log(e, true);
                }
                if (value > best) {
                    best = value;
                    best_pv = {*move};
                    best_pv.insert(best_pv.end(), child.begin(), child.end());
                }
                alpha = std::max(alpha, value);
            }
            result.best_move = best_pv.front();
            result.pv = std::move(best_pv);
            result.score = best;
            result.depth = depth;
            result.nodes = searcher.nodes;
            result.elapsed_ms = searcher.elapsed();
            result.stats = searcher.stats;
            searcher.completed_depth = depth;
            SearchEvent event{"search.iteration"}; event.depth = depth; event.move = result.best_move;
            event.score = best; event.moves = result.pv; event.node_delta = searcher.nodes-iteration_nodes;
            event.elapsed_delta_ms = searcher.elapsed()-iteration_elapsed; searcher.log(event);
            if (observer) observer(result);
            if (best > mate - 1000) { stop_reason = "mate_found"; break; }
        }
    } catch (const Interrupted& interruption) {
        stop_reason = interruption.reason;
        result.reason = stop_reason == "cancelled" ? "cancelled" : "limit";
    }
    result.nodes = searcher.nodes;
    result.elapsed_ms = searcher.elapsed();
    result.stats = searcher.stats;
    SearchEvent event{"search.end"}; event.depth = result.depth; event.move = result.best_move;
    event.score = result.score; event.moves = result.pv; event.reason = stop_reason; searcher.log(event);
    return result;
}
}  // namespace gomoku
