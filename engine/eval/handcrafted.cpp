#include "gomoku/evaluator.h"
#include <algorithm>
#include <array>

namespace gomoku {
int HandcraftedEvaluator::evaluate(const Position& position) const {
    constexpr std::array<int, 6> weights{0, 2, 12, 90, 800, 20000};
    constexpr std::array<Move, 4> directions{{{1, 0}, {0, 1}, {1, 1}, {1, -1}}};
    int score = 0;
    for (int y = 0; y < position.size(); ++y) {
        for (int x = 0; x < position.size(); ++x) {
            for (auto d : directions) {
                if (!position.contains({x + 4 * d.x, y + 4 * d.y})) continue;
                int black = 0, white = 0;
                for (int i = 0; i < 5; ++i) {
                    const auto color = position.at({x + i * d.x, y + i * d.y});
                    black += color == Color::black;
                    white += color == Color::white;
                }
                if (white == 0) score += weights[black];
                if (black == 0) score -= weights[white];
            }
        }
    }
    // Keep heuristic values strictly below proven tactical scores.
    score = std::clamp(score, -50000, 50000);
    return position.turn() == Color::black ? score : -score;
}
}  // namespace gomoku
