#pragma once

#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

namespace gomoku {

enum class Color : std::uint8_t { empty, black, white };
enum class Rule { freestyle, standard };
struct Move {
    int x;
    int y;
    bool operator==(const Move&) const = default;
};
struct Stone { Move move; Color color; };

Color opposite(Color color);
Rule parse_rule(std::string_view name);
std::string_view rule_name(Rule rule);
std::string_view color_name(Color color);

// Owns game semantics only. No I/O, model, protocol or UI dependencies.
class Position {
public:
    explicit Position(int size = 15, Rule rule = Rule::freestyle);
    static Position from_stones(int size, Rule rule, std::span<const Stone> stones, Color turn);

    int size() const { return size_; }
    Rule rule() const { return rule_; }
    Color turn() const { return turn_; }
    Color winner() const { return winner_; }
    int ply() const { return ply_; }
    bool full() const { return ply_ == size_ * size_; }
    bool terminal() const { return winner_ != Color::empty || full(); }
    std::uint64_t hash() const { return hash_; }
    bool contains(Move move) const;
    Color at(Move move) const;
    bool legal(Move move) const;
    bool wins(Move move, Color color) const;
    void play(Move move);
    void undo();
    std::vector<Move> candidates() const;
    std::string_view status() const;

private:
    struct Undo { Move move; Color winner; };
    int size_;
    Rule rule_;
    Color turn_ = Color::black;
    Color winner_ = Color::empty;
    int ply_ = 0;
    std::uint64_t hash_;
    std::vector<Color> board_;
    std::vector<Undo> history_;
    int index(Move move) const { return move.y * size_ + move.x; }
    int line_length(Move move, Color color, int dx, int dy) const;
};

}  // namespace gomoku
