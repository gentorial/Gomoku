#include "gomoku/nnue.h"
#include "gomoku/search.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>

using namespace gomoku;
using nlohmann::json;
int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::invalid_argument("Usage: gomoku-nnue-check weights.gnn reference-vectors.json");
        auto model = NnueModel::load_file(argv[1]);
        std::ifstream input(argv[2]);
        const auto reference = json::parse(input);
        if (reference.at("format") != "line-nnue-reference-v1") throw std::invalid_argument("Invalid reference format");
        NnueEvaluator evaluator(model), rebuilt(model);
        int values = 0, policies = 0, increments = 0, vectors = 0;
        std::mt19937 random(72);
        for (const auto& vector : reference.at("vectors")) {
            Position position(model->size(), model->rule());
            if (vector.contains("board")) {
                std::vector<Stone> stones;
                for (int y = 0; y < position.size(); ++y) for (int x = 0; x < position.size(); ++x) {
                    const int color = vector.at("board").at(y).at(x).get<int>();
                    if (color) stones.push_back({{x, y}, static_cast<Color>(color)});
                }
                position = Position::from_stones(model->size(), model->rule(), stones,
                    static_cast<Color>(vector.at("toMove").get<int>()));
            } else {
                for (const auto& move : vector.at("moves")) position.play({move.at("x"), move.at("y")});
            }
            std::vector<Move> all;
            for (int y = 0; y < position.size(); ++y) for (int x = 0; x < position.size(); ++x) all.push_back({x, y});
            evaluator.reset(position);
            const auto expected_value = vector.at("valueLogits").get<std::array<double, 3>>();
            const auto expected_policy = vector.at("policyLogits").get<std::vector<double>>();
            if (evaluator.value_logits(position) != expected_value || evaluator.move_scores(position, all) != expected_policy)
                throw std::runtime_error("Reference mismatch at vector " + std::to_string(vectors));
            values += 3; policies += static_cast<int>(all.size()); ++vectors;
            int depth = 0;
            for (; depth < 12 && !position.terminal(); ++depth) {
                const auto candidates = position.candidates();
                const auto move = candidates[random()%candidates.size()];
                position.play(move); evaluator.push(position, move); rebuilt.reset(position);
                if (evaluator.value_logits(position) != rebuilt.value_logits(position) ||
                    evaluator.move_scores(position, all) != rebuilt.move_scores(position, all))
                    throw std::runtime_error("Incremental NNUE differs from full refresh");
                ++increments;
            }
            while (depth-- > 0) {
                evaluator.pop(); position.undo(); rebuilt.reset(position);
                if (evaluator.value_logits(position) != rebuilt.value_logits(position) ||
                    evaluator.move_scores(position, all) != rebuilt.move_scores(position, all))
                    throw std::runtime_error("NNUE undo differs from full refresh");
                ++increments;
            }
            const std::atomic_bool cancelled{false};
            const auto result = search(position, evaluator, {10000, 6, 20}, cancelled);
            if (!position.terminal() && (result.reason != "limit" && result.reason != "completed"))
                throw std::runtime_error("Invalid interrupted search result");
            if (evaluator.value_logits(position) != expected_value || evaluator.move_scores(position, all) != expected_policy)
                throw std::runtime_error("Search interruption corrupted NNUE accumulator");
        }
        std::cout << json{{"vectors", vectors}, {"valueLogits", values}, {"policyLogits", policies},
            {"incrementalAndUndoChecks", increments}, {"maxAbsoluteError", 0}}.dump() << '\n';
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
