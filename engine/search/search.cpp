#include "gomoku/search.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace gomoku {
namespace {
constexpr int mate = 100000;
struct Interrupted {};
using Clock = std::chrono::steady_clock;

struct Search {
    Evaluator& evaluator;
    const SearchLimits& limits;
    const std::atomic_bool& cancelled;
    Clock::time_point start = Clock::now();
    std::uint64_t nodes = 0;
    int elapsed() const {
        return static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count());
    }
    void check() const {
        if (cancelled.load(std::memory_order_relaxed) || elapsed() >= limits.time_ms ||
            (limits.max_nodes != 0 && nodes >= limits.max_nodes)) throw Interrupted{};
    }
    std::vector<Move> ordered(const Position& position, std::optional<Move> preferred = {}) {
        struct Ranked { Move move; int priority; double policy; int heuristic; };
        std::vector<Ranked> ranked;
        const auto candidates = position.candidates();
        check();
        const auto policy = evaluator.move_scores(position, candidates);
        for (std::size_t i = 0; i < candidates.size(); ++i) {
            const Move move = candidates[i];
            check();
            int score = 0;
            const int priority = position.wins(move, position.turn()) ? 3 :
                position.wins(move, opposite(position.turn())) ? 2 : preferred == move ? 1 : 0;
            for (int dy = -2; dy <= 2; ++dy) {
                for (int dx = -2; dx <= 2; ++dx) {
                    Move p{move.x + dx, move.y + dy};
                    if (position.contains(p) && position.at(p) != Color::empty)
                        score += 10 - std::abs(dx) - std::abs(dy);
                }
            }
            score -= std::abs(move.x - position.size() / 2) + std::abs(move.y - position.size() / 2);
            ranked.push_back({move, priority, policy.empty() ? 0 : policy.at(i), score});
        }
        std::stable_sort(ranked.begin(), ranked.end(), [](const auto& a, const auto& b) {
            if (a.priority != b.priority) return a.priority > b.priority;
            if (a.policy != b.policy) return a.policy > b.policy;
            return a.heuristic > b.heuristic;
        });
        // A deliberately selective baseline, not a complete tactical solver.
        // All immediate wins and blocks are kept ahead of quiet candidates.
        std::vector<Move> moves;
        for (std::size_t i = 0; i < ranked.size(); ++i) {
            if (i >= 16 && ranked[i].priority < 2) break;
            moves.push_back(ranked[i].move);
        }
        return moves;
    }
    int negamax(Position& position, int depth, int alpha, int beta, int ply, std::vector<Move>& pv) {
        check();
        ++nodes;
        if (position.winner() != Color::empty) return -mate + ply;
        if (position.full()) return 0;
        if (depth == 0) return evaluator.evaluate(position);
        int best = -mate - 1;
        for (Move move : ordered(position)) {
            position.play(move);
            evaluator.push(position, move);
            std::vector<Move> child;
            int value;
            try {
                value = -negamax(position, depth - 1, -beta, -alpha, ply + 1, child);
            } catch (...) {
                evaluator.pop();
                position.undo();
                throw;
            }
            evaluator.pop();
            position.undo();
            if (value > best) {
                best = value;
                pv = {move};
                pv.insert(pv.end(), child.begin(), child.end());
            }
            alpha = std::max(alpha, value);
            if (alpha >= beta) break;
        }
        return best;
    }
};
}

SearchResult search(const Position& root, Evaluator& evaluator, const SearchLimits& limits,
                    const std::atomic_bool& cancelled, const SearchObserver& observer) {
    if (limits.time_ms < 0 || limits.max_depth < 1 || limits.max_depth > 64)
        throw std::invalid_argument("Invalid search limits");
    Search searcher{evaluator, limits, cancelled};
    SearchResult result;
    evaluator.reset(root);
    if (root.terminal()) {
        result.reason = "terminal";
        result.score = root.winner() == Color::empty ? 0 : -mate;
        return result;
    }
    const auto fallback = root.candidates();
    result.best_move = fallback.front();
    // Even an immediate timeout returns a legal move; prefer an immediate win/block.
    for (auto move : fallback) {
        if (root.wins(move, opposite(root.turn()))) result.best_move = move;
    }
    for (auto move : fallback) {
        if (root.wins(move, root.turn())) { result.best_move = move; break; }
    }
    result.pv = {*result.best_move};
    result.score = evaluator.evaluate(root);
    Position position = root;
    try {
        for (int depth = 1; depth <= limits.max_depth; ++depth) {
            int best = -mate - 1, alpha = -mate - 1;
            std::vector<Move> best_pv;
            for (auto move : searcher.ordered(position, result.depth > 0 ? result.best_move : std::nullopt)) {
                searcher.check();
                position.play(move);
                evaluator.push(position, move);
                std::vector<Move> child;
                int value;
                try {
                    value = -searcher.negamax(position, depth - 1, -mate - 1, -alpha, 1, child);
                } catch (...) {
                    evaluator.pop();
                    position.undo();
                    throw;
                }
                evaluator.pop();
                position.undo();
                if (value > best) {
                    best = value;
                    best_pv = {move};
                    best_pv.insert(best_pv.end(), child.begin(), child.end());
                }
                alpha = std::max(alpha, value);
            }
            result.best_move = best_pv.front();
            result.pv = best_pv;
            result.score = best;
            result.depth = depth;
            result.nodes = searcher.nodes;
            result.elapsed_ms = searcher.elapsed();
            if (observer) observer(result);
            if (best > mate - 1000) break;
        }
    } catch (const Interrupted&) {
        result.reason = cancelled.load() ? "cancelled" : "limit";
    }
    result.nodes = searcher.nodes;
    result.elapsed_ms = searcher.elapsed();
    return result;
}
}  // namespace gomoku
