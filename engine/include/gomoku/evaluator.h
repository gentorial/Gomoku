#pragma once
#include "gomoku/position.h"

namespace gomoku {
// One evaluator instance per search session. NNUE can own an accumulator stack.
// reset -> [push, evaluate, pop]*; evaluation is always side-to-move relative.
class Evaluator {
public:
    virtual ~Evaluator() = default;
    virtual std::string_view name() const = 0;
    virtual void reset(const Position&) {}
    virtual void push(const Position&, Move) {}
    virtual void pop() {}
    virtual int evaluate(const Position& position) const = 0;
    // Optional policy logits in candidate order. Search retains all immediate
    // wins/blocks; an empty vector keeps geometric ordering of quiet moves.
    virtual std::vector<double> move_scores(const Position&, std::span<const Move>) const { return {}; }
};
class HandcraftedEvaluator final : public Evaluator {
public:
    std::string_view name() const override { return "handcrafted-v1"; }
    int evaluate(const Position& position) const override;
};
}  // namespace gomoku
