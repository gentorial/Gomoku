#pragma once
#include "gomoku/evaluator.h"
#include <array>
#include <memory>
#include <string>
#include <vector>

namespace gomoku {
// Immutable, validated line11-dual-v1 weights, shared by search sessions.
// No dependency on JSON, Python, a GPU, or the host application's transport.
class NnueModel {
public:
    static constexpr std::size_t max_bytes = 512ULL * 1024 * 1024;
    static std::shared_ptr<const NnueModel> load(std::span<const std::uint8_t> bytes);
    static std::shared_ptr<const NnueModel> load_file(const std::string& path);
    bool supports(const Position& position) const;
    bool supports_size(int size) const { return (size == 15 && (sizes_ & 1)) || (size == 20 && (sizes_ & 2)); }
    std::vector<int> sizes() const;
    Rule rule() const { return rule_; }
    std::size_t bytes() const { return bytes_; }
private:
    friend class NnueEvaluator;
    int channels_ = 0, value_hidden_ = 0, policy_hidden_ = 0;
    std::uint32_t sizes_ = 0; // Bit 0: 15x15, bit 1: 20x20.
    Rule rule_ = Rule::freestyle;
    std::size_t bytes_ = 0;
    std::array<std::vector<std::int16_t>, 14> tensors_;
};

class NnueEvaluator final : public Evaluator {
public:
    explicit NnueEvaluator(std::shared_ptr<const NnueModel> model);
    ~NnueEvaluator() override;
    std::string_view name() const override { return "line11-nnue-v1"; }
    void reset(const Position& position) override;
    void push(const Position& position, Move move) override;
    void pop() override;
    int evaluate(const Position& position) const override;
    std::vector<double> move_scores(const Position& position, std::span<const Move> moves) const override;
    std::array<double, 3> value_logits(const Position& position) const;
private:
    struct State;
    std::unique_ptr<State> state_;
};
} // namespace gomoku
