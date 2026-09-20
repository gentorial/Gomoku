#include "gomoku/position.h"
#include "gomoku/profile.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace gomoku {
namespace {
constexpr std::array<Move, 4> directions{{{1, 0}, {0, 1}, {1, 1}, {1, -1}}};
std::uint64_t mix(std::uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}
std::uint64_t stone_key(int index, Color color) {
    return mix(10 + index * 2 + static_cast<int>(color));
}
constexpr std::uint64_t turn_key = 0x12343a489f574bc1ULL;
}

Color opposite(Color color) {
    if (color == Color::empty) throw std::invalid_argument("Empty has no opponent");
    return color == Color::black ? Color::white : Color::black;
}
Rule parse_rule(std::string_view name) {
    if (name == "freestyle") return Rule::freestyle;
    if (name == "standard") return Rule::standard;
    throw std::invalid_argument("Unsupported rule; supported: freestyle, standard");
}
std::string_view rule_name(Rule rule) {
    return rule == Rule::freestyle ? "freestyle" : "standard";
}
std::string_view color_name(Color color) {
    if (color == Color::black) return "black";
    if (color == Color::white) return "white";
    return "empty";
}

Position::Position(int size, Rule rule)
    : size_(size), rule_(rule), hash_(mix(1000 + size * 2 + static_cast<int>(rule))) {
    if (size != 15 && size != 20) throw std::invalid_argument("Supported board sizes: 15, 20");
    board_.resize(size * size, Color::empty);
}
bool Position::contains(Move move) const {
    return move.x >= 0 && move.x < size_ && move.y >= 0 && move.y < size_;
}
Color Position::at(Move move) const {
    if (!contains(move)) throw std::invalid_argument("Move outside board");
    return board_[index(move)];
}
bool Position::legal(Move move) const {
    return !terminal() && contains(move) && at(move) == Color::empty;
}
int Position::line_length(Move move, Color color, int dx, int dy) const {
    int length = 1;
    for (int sign : {-1, 1}) {
        Move p{move.x + sign * dx, move.y + sign * dy};
        while (contains(p) && at(p) == color) {
            ++length;
            p.x += sign * dx;
            p.y += sign * dy;
        }
    }
    return length;
}
bool Position::wins(Move move, Color color) const {
    if (!contains(move) || color == Color::empty) return false;
    if (at(move) != Color::empty && at(move) != color) return false;
    for (auto d : directions) {
        const int length = line_length(move, color, d.x, d.y);
        if (rule_ == Rule::freestyle ? length >= 5 : length == 5) return true;
    }
    return false;
}
void Position::play(Move move) {
    GOMOKU_SCOPE(play);
    if (!legal(move)) throw std::invalid_argument("Illegal move or game already finished");
    history_.push_back({move, winner_});
    board_[index(move)] = turn_;
    hash_ ^= stone_key(index(move), turn_) ^ turn_key;
    ++ply_;
    if (wins(move, turn_)) winner_ = turn_;
    turn_ = opposite(turn_);
}
void Position::undo() {
    GOMOKU_SCOPE(undo);
    if (history_.empty()) throw std::invalid_argument("No move to undo");
    const auto last = history_.back();
    history_.pop_back();
    turn_ = opposite(turn_);
    hash_ ^= stone_key(index(last.move), turn_) ^ turn_key;
    board_[index(last.move)] = Color::empty;
    winner_ = last.winner;
    --ply_;
}
Position Position::from_stones(int size, Rule rule, std::span<const Stone> stones, Color turn) {
    if (turn == Color::empty) throw std::invalid_argument("Missing side to move");
    Position position(size, rule);
    for (const auto& stone : stones) {
        if (!position.contains(stone.move) || stone.color == Color::empty ||
            position.at(stone.move) != Color::empty) {
            throw std::invalid_argument("Invalid or duplicate stone in position");
        }
        position.board_[position.index(stone.move)] = stone.color;
        position.hash_ ^= stone_key(position.index(stone.move), stone.color);
        ++position.ply_;
    }
    position.turn_ = turn;
    if (turn == Color::white) position.hash_ ^= turn_key;
    for (const auto& stone : stones) {
        if (position.wins(stone.move, stone.color)) {
            if (position.winner_ != Color::empty && position.winner_ != stone.color)
                throw std::invalid_argument("Both sides cannot win");
            position.winner_ = stone.color;
        }
    }
    return position;
}
std::vector<Move> Position::candidates() const {
    GOMOKU_SCOPE(candidates);
    if (terminal()) return {};
    if (ply_ == 0) return {{size_ / 2, size_ / 2}};
    std::vector<Move> moves;
    for (int y = 0; y < size_; ++y) {
        for (int x = 0; x < size_; ++x) {
            if (at({x, y}) != Color::empty) continue;
            bool nearby = false;
            for (int dy = -2; dy <= 2 && !nearby; ++dy) {
                for (int dx = -2; dx <= 2; ++dx) {
                    Move p{x + dx, y + dy};
                    if (contains(p) && at(p) != Color::empty) { nearby = true; break; }
                }
            }
            if (nearby) moves.push_back({x, y});
        }
    }
    return moves;
}
std::string_view Position::status() const {
    if (winner_ == Color::black) return "black_win";
    if (winner_ == Color::white) return "white_win";
    return full() ? "draw" : "playing";
}
}  // namespace gomoku
