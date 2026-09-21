#include "gomoku/vcf.h"
#include <stdexcept>

namespace gomoku {
namespace {
struct Played {
    Position& position;
    Threats& threats;
    Move move;
    Played(Position& p, Threats& t, Move m) : position(p), threats(t), move(m) {
        const auto color = p.turn(); p.play(m); t.play(m, color);
    }
    ~Played() { threats.undo(move); position.undo(); }
};
struct Solver {
    const VcfLimits& limits;
    const std::function<void()>& visit;
    std::uint64_t nodes = 0;
    VcfStatus attack(Position& p, Threats& t, int left, std::vector<Move>& pv) {
        if (nodes >= limits.max_nodes || left < 1) return VcfStatus::unknown;
        if (visit) visit();
        ++nodes;
        if (p.terminal()) return VcfStatus::no_win;
        const auto self = p.turn(), oppo = opposite(self);
        if (t.winning_count(self)) {
            pv = {t.winning_moves(self).front()};
            return VcfStatus::win;
        }
        if (t.winning_count(oppo) > 1) return VcfStatus::no_win;
        auto moves = t.winning_count(oppo) ? t.winning_moves(oppo) : t.four_moves(self);
        bool unknown = false;
        for (auto move : moves) {
            if (!t.four(move, self)) continue;
            if (visit) visit(); // Deadlines remain responsive even in a wide attack list.
            Played attackMove(p, t, move);
            // A counter-five beats an attack, even if the attacker makes two fours.
            if (t.winning_count(oppo)) continue;
            const auto replies = t.winning_moves(self);
            if (replies.empty()) continue;
            if (left < 3) { unknown = true; continue; }
            if (replies.size() >= 2) {
                pv = {move, replies[0], replies[1]};
                return VcfStatus::win;
            }
            std::vector<Move> continuation;
            VcfStatus status;
            {
                Played defendMove(p, t, replies.front());
                status = attack(p, t, left-2, continuation);
            }
            if (status == VcfStatus::win) {
                pv = {move, replies.front()};
                pv.insert(pv.end(), continuation.begin(), continuation.end());
                return status;
            }
            unknown |= status == VcfStatus::unknown;
            if (nodes >= limits.max_nodes) return VcfStatus::unknown;
        }
        return unknown ? VcfStatus::unknown : VcfStatus::no_win;
    }
};
}
VcfResult solve_vcf(Position& position, Threats& threats, const VcfLimits& limits,
                    const std::function<void()>& visit) {
    if (limits.max_plies < 1 || limits.max_plies > 400) throw std::invalid_argument("Invalid VCF depth");
    Solver solver{limits, visit};
    VcfResult result;
    result.status = solver.attack(position, threats, limits.max_plies, result.pv);
    result.nodes = solver.nodes;
    return result;
}
}  // namespace gomoku
