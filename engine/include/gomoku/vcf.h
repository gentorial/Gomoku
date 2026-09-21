#pragma once
#include "gomoku/threats.h"
#include <functional>

namespace gomoku {
enum class VcfStatus { win, no_win, unknown };
struct VcfLimits { int max_plies = 32; std::uint64_t max_nodes = 1024; };
struct VcfResult {
    VcfStatus status = VcfStatus::no_win;
    std::vector<Move> pv;
    std::uint64_t nodes = 0;
};
// Proof search for the side to move. no_win excludes only continuous-four wins,
// not other winning strategies. Unknown never constitutes a loss or a draw.
// The full winning PV and all board/cache mutations are unwound on every exit.
VcfResult solve_vcf(Position& position, Threats& threats, const VcfLimits& limits = {},
                    const std::function<void()>& visit = {});
}  // namespace gomoku
