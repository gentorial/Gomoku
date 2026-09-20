#include "gomoku/search.h"
#include <iostream>
#include <random>
#include <stdexcept>

using namespace gomoku;
namespace {
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
// Hash-derived values deliberately make ordering imperfect, with many PVS
// fail-high/research paths. The value depends only on position, never history.
struct CheckedEvaluator : Evaluator {
    std::vector<std::uint64_t> hashes;
    mutable int evaluations = 0;
    int throw_after = 0;
    int bias = 0;
    std::string_view name() const override { return "search-test"; }
    void reset(const Position& p) override { hashes = {p.hash()}; evaluations = 0; }
    void push(const Position& p, Move) override { hashes.push_back(p.hash()); }
    void pop() override { require(hashes.size() > 1, "evaluator stack underflow"); hashes.pop_back(); }
    int evaluate(const Position& p) const override {
        require(hashes.back() == p.hash(), "evaluation must match the active accumulator");
        if (throw_after && ++evaluations == throw_after) throw std::runtime_error("injected evaluation failure");
        return static_cast<int>((p.hash() >> 32) % 1001) - 500 + bias;
    }
    std::vector<double> move_scores(const Position& p, std::span<const Move> moves) const override {
        require(hashes.back() == p.hash(), "policy must match the active accumulator");
        std::vector<double> scores;
        for (auto move : moves) scores.push_back((move.x*17+move.y*13)%23);
        return scores;
    }
};
void validate_pv(Position position, CheckedEvaluator& evaluator, const SearchResult& result) {
    require(result.best_move && result.pv.front() == result.best_move, "PV must start with best move");
    int ply = 0;
    for (auto move : result.pv) {
        require(position.legal(move), "PV moves must be legal");
        position.play(move);
        ++ply;
    }
    require(position.terminal() || ply == result.depth, "completed PV must reach the requested depth");
    evaluator.reset(position);
    const int value = position.winner() != Color::empty ? -100000 + ply :
        position.full() ? 0 : evaluator.evaluate(position);
    require((ply % 2 ? -value : value) == result.score, "PV leaf must agree with the exact root score");
}
}

int main() {
    try {
        std::atomic_bool cancel{false};
        std::mt19937 random(7139);
        std::uint64_t hits = 0, cutoffs = 0, researches = 0, preferred = 0;
        for (int sample = 0; sample < 14; ++sample) {
            Position position(sample%2 ? 20 : 15, sample%3 ? Rule::freestyle : Rule::standard);
            for (int n = 0; n < 1+sample*2 && !position.terminal(); ++n) {
                const auto moves = position.candidates();
                position.play(moves[random()%moves.size()]);
            }
            if (position.terminal()) continue;
            const auto hash = position.hash();
            const SearchLimits limits{60000, sample < 2 ? 4 : 3, 0};
            CheckedEvaluator evaluator;
            const auto reference = search(position, evaluator, limits, cancel, {}, {false, false});
            require(reference.reason == "completed", "reference must finish");
            validate_pv(position, evaluator, reference);
            for (const SearchOptions options : {SearchOptions{true, true}, {true, false},
                                                {false, true}, {true, true, 1}}) {
                const auto result = search(position, evaluator, limits, cancel, {}, options);
                require(result.reason == "completed", "optimized search must finish");
                require(result.score == reference.score && result.depth == reference.depth,
                        "TT/PVS and forced collisions must preserve full-window minimax scores");
                require(position.hash() == hash && evaluator.hashes.size() == 1 && evaluator.hashes.back() == hash,
                        "search must restore board and accumulator on completion");
                validate_pv(position, evaluator, result);
                hits += result.stats.tt_hits; cutoffs += result.stats.tt_cutoffs;
                researches += result.stats.pvs_researches; preferred += result.stats.preferred_cutoffs;
            }
            // A new model/evaluation at the same root must not see stale TT scores.
            evaluator.bias = 713;
            const auto changed = search(position, evaluator, {60000, 2, 0}, cancel);
            const auto changed_reference = search(position, evaluator, {60000, 2, 0}, cancel, {}, {false, false});
            require(changed.score == changed_reference.score, "TT must be scoped to one evaluator/search");
        }
        require(hits && cutoffs && researches && preferred, "exercise TT, PVS, and deferred policy cutoffs");

        Position opening;
        opening.play({7, 7});
        CheckedEvaluator evaluator;
        for (const std::uint64_t budget : {1, 15, 100, 777}) {
            const auto result = search(opening, evaluator, {60000, 8, budget}, cancel);
            require(result.reason == "limit" && result.nodes <= budget && opening.legal(*result.best_move),
                    "node limit must return a legal completed iteration/fallback");
            require(evaluator.hashes.size() == 1 && evaluator.hashes.back() == opening.hash(),
                    "node limit must unwind every accumulator frame");
        }
        evaluator.throw_after = 40;
        bool failed = false;
        try { search(opening, evaluator, {60000, 6, 0}, cancel); }
        catch (const std::runtime_error&) { failed = true; }
        require(failed && evaluator.hashes.size() == 1 && evaluator.hashes.back() == opening.hash(),
                "evaluator exceptions must unwind both search state stacks");
        evaluator.throw_after = 0;
        const auto interrupted = search(opening, evaluator, {60000, 8, 0}, cancel,
            [&](const SearchResult&) { cancel = true; });
        require(interrupted.reason == "cancelled" && interrupted.depth == 1 && evaluator.hashes.size() == 1,
                "cancellation between iterations must retain the last result and restore state");
        std::cout << "TT/PVS reference, collisions, PV, model isolation and interruption tests passed; "
                  << cutoffs << " TT cutoffs, " << researches << " PVS re-searches\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
