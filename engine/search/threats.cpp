#include "gomoku/threats.h"

namespace gomoku {
namespace {
constexpr std::array<Move, 4> directions{{{1,0}, {0,1}, {1,1}, {1,-1}}};
constexpr std::array<unsigned, 10> powers{1,3,9,27,81,243,729,2187,6561,19683};
constexpr int exponent(int offset) { return offset < 0 ? offset + 5 : offset + 4; }
struct Tables {
    // Center is empty and omitted: ten ternary cells, own/empty/opponent-or-edge.
    std::array<std::array<unsigned char, 59049>, 2> values{};
    Tables() {
        for (unsigned code = 0; code < 59049; ++code) {
            std::array<int, 11> cells{};
            unsigned rest = code;
            for (int i = 0; i < 11; ++i) if (i != 5) { cells[i] = rest % 3; rest /= 3; }
            for (int standard = 0; standard < 2; ++standard) {
                bool win = false;
                unsigned replies = 0;
                for (int start = 1; start <= 5; ++start) {
                    if (standard && (cells[start-1] == 1 || cells[start+5] == 1)) continue;
                    int blanks = 0, reply = -1;
                    bool blocked = false;
                    for (int i = start; i < start+5; ++i) {
                        blocked |= cells[i] == 2;
                        if (cells[i] == 0) { ++blanks; if (i != 5) reply = i; }
                    }
                    if (blocked) continue;
                    win |= blanks == 1;
                    if (blanks == 2) replies |= 1u << reply;
                }
                const bool two = replies && (replies & (replies-1));
                values[standard][code] = static_cast<unsigned char>((win ? 1 : 0) | (replies ? 2 : 0) | (two ? 4 : 0));
            }
        }
    }
};
const auto& table(Rule rule) {
    static const Tables tables;
    return tables.values[rule == Rule::standard ? 1 : 0];
}
}

bool Threats::contains(Move p) const { return p.x >= 0 && p.y >= 0 && p.x < size_ && p.y < size_; }
Threats::Threats(const Position& position) : size_(position.size()), rule_(position.rule()) {
    for (int y = 0; y < size_; ++y) for (int x = 0; x < size_; ++x) board_[index({x,y})] = position.at({x,y});
    for (int y = 0; y < size_; ++y) for (int x = 0; x < size_; ++x) {
        const int point = index({x,y});
        for (int d = 0; d < 4; ++d) {
            for (int offset = -5; offset <= 5; ++offset) if (offset) {
                const Move p{x+offset*directions[d].x, y+offset*directions[d].y};
                const bool inside = contains(p);
                const Color color = inside ? board_[index(p)] : Color::empty;
                for (int s = 0; s < 2; ++s) {
                    const unsigned digit = !inside ? 2 : color == Color::empty ? 0 : side(color) == s ? 1 : 2;
                    codes_[point*4+d][s] += digit * powers[exponent(offset)];
                }
            }
            refresh(point, d);
        }
    }
}
void Threats::refresh(int point, int direction) {
    const auto& lookup = table(rule_);
    auto& line = lines_[point*4+direction];
    line = board_[point] == Color::empty ? static_cast<unsigned char>(lookup[codes_[point*4+direction][0]] |
        (lookup[codes_[point*4+direction][1]] << 4)) : 0;
    unsigned combined = 0;
    for (int s = 0; s < 2; ++s) {
        unsigned feature = 0;
        int replies = 0;
        for (int d = 0; d < 4; ++d) {
            const unsigned f = (lines_[point*4+d] >> (s*4)) & 7;
            feature |= f & 1;
            replies += f & 4 ? 2 : f & 2 ? 1 : 0;
        }
        feature |= (replies ? 2 : 0) | (replies >= 2 ? 4 : 0);
        const unsigned old = (features_[point] >> (s*4)) & 7;
        wins_[s] += int(bool(feature & 1)) - int(bool(old & 1));
        fours_[s] += int(bool(feature & 2)) - int(bool(old & 2));
        combined |= feature << (s*4);
    }
    features_[point] = static_cast<unsigned char>(combined);
}
void Threats::change(Move move, Color color, int sign) {
    board_[index(move)] = sign > 0 ? color : Color::empty;
    for (int d = 0; d < 4; ++d) {
        for (int distance = -5; distance <= 5; ++distance) {
            const Move center{move.x+distance*directions[d].x, move.y+distance*directions[d].y};
            if (!contains(center)) continue;
            const int point = index(center);
            if (distance) for (int s = 0; s < 2; ++s) {
                const unsigned delta = (side(color) == s ? 1 : 2) * powers[exponent(-distance)];
                if (sign > 0) codes_[point*4+d][s] += delta;
                else codes_[point*4+d][s] -= delta;
            }
            refresh(point, d);
        }
    }
}
void Threats::play(Move move, Color color) { change(move, color, 1); }
void Threats::undo(Move move) { change(move, board_[index(move)], -1); }
bool Threats::wins(Move move, Color color) const { return contains(move) && (features_[index(move)] & (1 << (side(color)*4))); }
bool Threats::four(Move move, Color color) const { return contains(move) && (features_[index(move)] & (2 << (side(color)*4))); }
std::vector<Move> Threats::moves(Color color, unsigned mask) const {
    std::vector<Move> result;
    mask <<= side(color)*4;
    for (int p = 0; p < size_*size_; ++p) if (features_[p] & mask) result.push_back({p%size_,p/size_});
    return result;
}
std::vector<Move> Threats::winning_moves(Color color) const { return moves(color, 1); }
std::vector<Move> Threats::four_moves(Color color) const { return moves(color, 2); }
}  // namespace gomoku
