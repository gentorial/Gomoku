#include "gomoku/search.h"
#include "gomoku/profile.h"
#include "gomoku/vcf.h"
#include <algorithm>
#include <bit>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>

namespace gomoku {
namespace {
constexpr int mate = 100000;
constexpr int infinity = mate + 1;
struct Interrupted {};
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
        if (cancelled.load(std::memory_order_relaxed) || elapsed() >= limits.time_ms ||
            (limits.max_nodes != 0 && nodes >= limits.max_nodes)) throw Interrupted{};
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
    bool threatened(const Position& position) const {
        return options.threat_filter && threats && threats->open_four_count(opposite(position.turn()));
    }
    // Replies to an opponent's open three: own fours, and moves after which the
    // opponent has no point left that gives it two winning points. Any other move
    // lets it make an unstoppable four. Only cells on a line within five of such a
    // point can break it, so only those are tried on the threat board.
    std::vector<Move> threat_replies(const Position& position) {
        const auto side = position.turn(), other = opposite(side);
        std::array<bool, 400> near{};
        for (const auto point : threats->open_four_moves(other))
            for (const Move d : {Move{1,0}, Move{0,1}, Move{1,1}, Move{1,-1}})
                for (int k = -5; k <= 5; ++k) {
                    const Move q{point.x + k*d.x, point.y + k*d.y};
                    if (position.contains(q)) near[q.y*position.size()+q.x] = true;
                }
        std::vector<Move> replies;
        for (const auto move : position.candidates()) {
            check();
            bool keep = threats->four(move, side);
            if (!keep && near[move.y*position.size()+move.x]) {
                threats->play(move, side);
                keep = !threats->open_four_count(other) && !threats->winning_count(other);
                threats->undo(move);
            }
            if (keep) replies.push_back(move);
        }
        return replies;
    }
    // With no reply to an open three, every move loses: the opponent makes a four
    // with two winning points, one is blocked, the other completes five. Returns
    // that line, or nothing if the board does not follow it (then search normally).
    std::optional<std::vector<Move>> forced_loss(Position& position) {
        const auto side = position.turn(), other = opposite(side);
        const auto attacks = threats->open_four_moves(other);
        if (attacks.empty() || threats->four_count(side) || threats->winning_count(side)) return std::nullopt;
        const Move first = position.legal(attacks.front()) ? attacks.front() : position.candidates().front();
        Played a(position, first, &*threats);
        const auto fours = threats->open_four_moves(other);
        if (fours.empty() || threats->winning_count(side)) return std::nullopt;
        Played b(position, fours.front(), &*threats);
        auto wins = threats->winning_moves(other);
        if (wins.size() < 2 || threats->winning_count(side)) return std::nullopt;
        Played c(position, wins.front(), &*threats);
        wins = threats->winning_moves(other);
        if (wins.empty() || !position.wins(wins.front(), other)) return std::nullopt;
        return std::vector<Move>{first, fours.front(), c.move, wins.front()};
    }
    // Moves are returned best first; `tactical` marks moves LMR must not reduce.
    std::vector<Move> ordered(const Position& position, std::vector<char>& tactical,
                              const std::vector<Move>* restricted = nullptr) {
        if (threats) {
            auto forced = threats->winning_count(position.turn()) ? threats->winning_moves(position.turn()) :
                threats->winning_count(opposite(position.turn())) ? threats->winning_moves(opposite(position.turn())) :
                std::vector<Move>{};
            if (!forced.empty()) { tactical.assign(forced.size(), 1); return forced; }
        }
        std::vector<Move> replies;
        if (!restricted && threatened(position)) replies = threat_replies(position);
        if (!restricted && !replies.empty()) restricted = &replies;
        struct Ranked { Move move; int priority; double policy; int heuristic; std::size_t ordinal; };
        std::vector<Ranked> ranked;
        // With no reply at all (only at the root), every move loses; rank them all.
        const auto candidates = restricted ? *restricted : position.candidates();
        ranked.reserve(candidates.size());
        check();
        const auto policy = evaluator.move_scores(position, candidates);
        std::size_t tactical_count = 0;
        {
            GOMOKU_SCOPE(ranking);
            for (std::size_t i = 0; i < candidates.size(); ++i) {
                const Move move = candidates[i];
                check();
                int score = 0;
                const int priority = position.wins(move, position.turn()) ? 3 :
                    position.wins(move, opposite(position.turn())) ? 2 : 0;
                tactical_count += priority != 0;
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
        const auto count = restricted ? ranked.size() : std::min(ranked.size(), std::max(std::size_t(16), tactical_count));
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
        tactical.clear();
        const auto side = position.turn(), other = opposite(side);
        for (std::size_t i = 0; i < count; ++i) {
            const auto move = ranked[i].move;
            moves.push_back(move);
            tactical.push_back(restricted || ranked[i].priority ||
                (threats && (threats->four(move, side) || threats->four(move, other))));
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
        std::vector<char> tactical;
        const std::vector<Move>* restricted = nullptr;
        bool last_tactical = true;
        Picker(Search& s, const Position& p, std::optional<Move> first, const std::vector<Move>* only = nullptr)
            : search(s), position(p), preferred(first), restricted(only) {}
        std::optional<Move> next() {
            // This is a previously searched move at the identical position,
            // so it belongs to the selected set even before policy is computed.
            if (!tried_preferred) { tried_preferred = true; if (preferred) return preferred; }
            if (!generated) { moves = search.ordered(position, tactical, restricted); generated = true; }
            while (index < moves.size()) {
                const auto move = moves[index];
                last_tactical = tactical[index++];
                if (move != preferred) return move;
            }
            return {};
        }
    };
    int reduction(int depth, int searched, bool pv) const {
        const double r = options.lmr_base + std::log(depth) * std::log(searched) / options.lmr_divisor;
        return std::max(0, static_cast<int>(r) - (pv ? 1 : 0));
    }
    int negamax(Position& position, int depth, int alpha, int beta, int ply,
                Move last_move, std::vector<Move>& pv) {
        check();
        ++nodes;
        pv.clear();
        if (position.winner() != Color::empty) return -mate + ply;
        if (position.full()) return 0;
        bool forced = false;
        if (threats) {
            const auto side = position.turn(), other = opposite(side);
            if (threats->winning_count(side)) {
                pv = {threats->winning_moves(side).front()};
                return mate - ply - 1;
            }
            const int danger = threats->winning_count(other);
            if (danger >= 2) {
                const auto points = threats->winning_moves(other);
                pv = {points[0], points[1]};
                return -mate + ply + 2;
            }
            forced = danger == 1;
            if (depth == 0 && !forced && threats->four_count(side)) {
                const auto proof = solve_vcf(position, *threats,
                    {options.vcf_max_plies, options.vcf_node_limit}, [&] {
                        check(); ++nodes; ++stats.vcf_nodes;
                    });
                if (proof.status == VcfStatus::win) {
                    ++stats.vcf_wins;
                    pv = proof.pv;
                    return mate - ply - static_cast<int>(pv.size());
                }
                stats.vcf_unknown += proof.status == VcfStatus::unknown;
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
                return value;
            }
        }
        // An open three against us leaves few replies; with none, the opponent
        // makes an unstoppable four next move and five two moves later.
        std::vector<Move> replies;
        if (depth > 0 && !forced && threatened(position)) {
            ++stats.threatened_nodes;
            replies = threat_replies(position);
            if (replies.empty()) {
                if (auto line = forced_loss(position)) {
                    ++stats.threat_losses;
                    pv = std::move(*line);
                    return -mate + ply + 4;
                }
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
        Picker picker(*this, position, preferred, replies.empty() ? nullptr : &replies);
        int searched = 0;
        const bool pv_node = beta - alpha > 1;
        while (const auto move = picker.next()) {
            std::vector<Move> child;
            int value;
            {
                Played played(position, *move, tactics());
                const int child_depth = std::max(depth-1, 0);
                if (options.pvs && searched > 0) {
                    // Policy-ordered late quiet moves are first refuted at reduced depth.
                    const int r = options.lmr && depth >= options.lmr_min_depth && searched >= options.lmr_min_moves &&
                        !forced && replies.empty() && !picker.last_tactical ? reduction(depth, searched, pv_node) : 0;
                    if (r > 0) {
                        ++stats.lmr_reductions;
                        value = -negamax(position, std::max(child_depth - r, 0), -alpha-1, -alpha, ply+1, *move, child);
                        if (value > alpha) {
                            ++stats.lmr_researches;
                            value = -negamax(position, child_depth, -alpha-1, -alpha, ply+1, *move, child);
                        }
                    } else value = -negamax(position, child_depth, -alpha-1, -alpha, ply+1, *move, child);
                    if (value > alpha && value < beta) {
                        ++stats.pvs_researches;
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
                if (!picker.generated) ++stats.preferred_cutoffs;
                break;
            }
        }
        store(position.hash(), depth, best, best >= beta ? Bound::lower :
            best <= original_alpha ? Bound::upper : Bound::exact, best_move, ply);
        return best;
    }
};
}

void set_search_option(SearchOptions& options, std::string_view assignment) {
    const auto split = assignment.find('=');
    if (split == std::string_view::npos || split == 0)
        throw std::invalid_argument("Search option must be name=value");
    const auto name = assignment.substr(0, split);
    const std::string text(assignment.substr(split + 1));
    std::size_t used = 0;
    double value = 0;
    try { value = std::stod(text, &used); } catch (const std::exception&) { used = 0; }
    if (text.empty() || used != text.size() || !std::isfinite(value))
        throw std::invalid_argument("Invalid value for search option " + std::string(name));
    const auto whole = [&](long long low, long long high) {
        if (value != std::floor(value) || value < low || value > high)
            throw std::invalid_argument("Search option " + std::string(name) + " is out of range");
        return static_cast<long long>(value);
    };
    if (name == "transpositions") options.transpositions = whole(0, 1);
    else if (name == "pvs") options.pvs = whole(0, 1);
    else if (name == "table_entries") options.table_entries = static_cast<std::size_t>(whole(1, 1 << 18));
    else if (name == "vcf") options.vcf = whole(0, 1);
    else if (name == "vcf_node_limit") options.vcf_node_limit = static_cast<std::uint64_t>(whole(0, 10000000));
    else if (name == "vcf_max_plies") options.vcf_max_plies = static_cast<int>(whole(1, 400));
    else if (name == "threat_filter") options.threat_filter = whole(0, 1);
    else if (name == "lmr") options.lmr = whole(0, 1);
    else if (name == "lmr_min_depth") options.lmr_min_depth = static_cast<int>(whole(1, 64));
    else if (name == "lmr_min_moves") options.lmr_min_moves = static_cast<int>(whole(1, 400));
    else if (name == "lmr_base" && value >= 0 && value <= 8) options.lmr_base = value;
    else if (name == "lmr_divisor" && value > 0 && value <= 16) options.lmr_divisor = value;
    else throw std::invalid_argument("Unknown or out-of-range search option " + std::string(assignment));
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
    if (options.lmr_min_depth < 1 || options.lmr_min_moves < 1 || !(options.lmr_base >= 0) ||
        !(options.lmr_divisor > 0))
        throw std::invalid_argument("Invalid LMR parameters");
    Search searcher(evaluator, limits, cancelled, options, root);
    SearchResult result;
    evaluator.reset(root);
    if (root.terminal()) {
        result.reason = "terminal";
        result.score = root.winner() == Color::empty ? 0 : -mate;
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
    Position position = root;
    try {
        for (int depth = 1; depth <= limits.max_depth; ++depth) {
            int best = -infinity, alpha = -infinity;
            std::vector<Move> best_pv;
            Search::Picker picker(searcher, position, result.depth > 0 ? result.best_move : std::nullopt);
            int searched = 0;
            while (const auto move = picker.next()) {
                searcher.check();
                std::vector<Move> child;
                int value;
                {
                    Played played(position, *move, searcher.tactics());
                    if (options.pvs && searched > 0) {
                        value = -searcher.negamax(position, depth-1, -alpha-1, -alpha, 1, *move, child);
                        if (value > alpha) {
                            ++searcher.stats.pvs_researches;
                            value = -searcher.negamax(position, depth-1, -infinity, -alpha, 1, *move, child);
                        }
                    } else value = -searcher.negamax(position, depth-1, -infinity, -alpha, 1, *move, child);
                }
                ++searched;
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
            if (observer) observer(result);
            if (best > mate - 1000) break;
        }
    } catch (const Interrupted&) {
        result.reason = cancelled.load() ? "cancelled" : "limit";
    }
    result.nodes = searcher.nodes;
    result.elapsed_ms = searcher.elapsed();
    result.stats = searcher.stats;
    return result;
}
}  // namespace gomoku
