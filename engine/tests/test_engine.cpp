#include "gomoku/search.h"
#include <iostream>
#include <random>
#include <stdexcept>

using namespace gomoku;
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
struct TrackingEvaluator : Evaluator {
    HandcraftedEvaluator base;
    int stack = 0;
    std::string_view name() const override { return "tracking"; }
    void reset(const Position&) override { stack = 0; }
    void push(const Position&, Move) override { ++stack; }
    void pop() override { --stack; }
    int evaluate(const Position& position) const override { return base.evaluate(position); }
};

int main() {
    try {
        Position position;
        const auto initial_hash = position.hash();
        std::mt19937 random(42);
        int played = 0;
        while (played < 40 && !position.terminal()) {
            const auto moves = position.candidates();
            position.play(moves[random() % moves.size()]);
            ++played;
        }
        while (played--) position.undo();
        require(position.hash() == initial_hash && position.ply() == 0 &&
                position.turn() == Color::black, "make/unmake must restore all state");
        require(Position(20).hash() != initial_hash, "hash must include board size");
        require(Position(15, Rule::standard).hash() != initial_hash, "hash must include rule");

        for (auto direction : std::vector<Move>{{1, 0}, {0, 1}, {1, 1}, {1, -1}}) {
            std::vector<Stone> stones;
            for (int i = 0; i < 4; ++i)
                stones.push_back({{4 + direction.x * i, 8 + direction.y * i}, Color::black});
            auto tactical = Position::from_stones(15, Rule::freestyle, stones, Color::black);
            HandcraftedEvaluator evaluator;
            std::atomic_bool cancel{false};
            const auto result = search(tactical, evaluator, {100, 2, 0}, cancel);
            require(result.best_move && tactical.wins(*result.best_move, Color::black),
                    "search must take immediate wins in every direction");
            require(tactical.ply() == 4, "search must not mutate the caller's position");
        }

        std::vector<Stone> five;
        for (int x = 3; x < 8; ++x) five.push_back({{x, 7}, Color::black});
        auto exact = Position::from_stones(15, Rule::standard, five, Color::white);
        require(exact.winner() == Color::black, "exact five wins in standard");
        five.push_back({{8, 7}, Color::black});
        auto overline = Position::from_stones(15, Rule::standard, five, Color::white);
        require(overline.winner() == Color::empty, "overline does not win in standard");
        require(Position::from_stones(15, Rule::freestyle, five, Color::white).winner() == Color::black,
                "overline wins in freestyle");

        std::vector<Stone> threat{{{3, 7}, Color::black}};
        for (int x = 4; x < 8; ++x) threat.push_back({{x, 7}, Color::white});
        auto defense = Position::from_stones(15, Rule::freestyle, threat, Color::black);
        HandcraftedEvaluator evaluator;
        std::atomic_bool cancel{false};
        auto result = search(defense, evaluator, {100, 2, 0}, cancel);
        require(result.best_move == Move{8, 7}, "must block a one-ended four");
        cancel = true;
        result = search(position, evaluator, {1000, 6, 0}, cancel);
        require(result.reason == "cancelled" && result.best_move && position.legal(*result.best_move),
                "cancelled search must return a legal fallback");
        cancel = false;
        result = search(position, evaluator, {0, 4, 0}, cancel);
        require(result.reason == "limit" && result.depth == 0, "zero-time search must not deepen");
        TrackingEvaluator tracking;
        search(position, tracking, {1000, 6, 15}, cancel);
        require(tracking.stack == 0, "node-limit interruption must restore evaluator stack");
        bool rejected = false;
        try { position.play({-1, 0}); } catch (const std::invalid_argument&) { rejected = true; }
        require(rejected, "out-of-bounds move must fail");
        std::cout << "Engine invariants and tactical tests passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
