#pragma once
#include "gomoku/position.h"
#include <array>

namespace gomoku {
// Incremental, exact five/four features. No network, move ordering or I/O.
// A four move leaves at least one immediate winning point after it is played.
class Threats {
public:
    explicit Threats(const Position& position);
    void play(Move move, Color color);
    void undo(Move move);
    int winning_count(Color color) const { return wins_[side(color)]; }
    int four_count(Color color) const { return fours_[side(color)]; }
    bool wins(Move move, Color color) const;
    bool four(Move move, Color color) const;
    std::vector<Move> winning_moves(Color color) const;
    std::vector<Move> four_moves(Color color) const;

private:
    int size_;
    Rule rule_;
    std::array<Color, 400> board_{};
    std::array<std::array<unsigned, 2>, 1600> codes_{};
    std::array<unsigned char, 1600> lines_{};
    std::array<unsigned char, 400> features_{};
    std::array<int, 2> wins_{}, fours_{};
    static int side(Color color) { return color == Color::black ? 0 : 1; }
    int index(Move move) const { return move.y * size_ + move.x; }
    bool contains(Move move) const;
    void change(Move move, Color color, int sign);
    void refresh(int point, int direction);
    std::vector<Move> moves(Color color, unsigned mask) const;
};
}  // namespace gomoku
