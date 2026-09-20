#include "gomoku/search.h"
#include "gomoku/nnue.h"
#include <algorithm>
#include <climits>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

using namespace gomoku;
namespace {
struct Config {
    int rule = 0;
    long long turn_ms = 1000;
    long long left_ms = INT_MAX;
    long long memory = 0;
    std::shared_ptr<const NnueModel> model;
};
Move coordinate(std::string value) {
    std::replace(value.begin(), value.end(), ',', ' ');
    std::istringstream stream(value);
    Move move{};
    std::string extra;
    if (!(stream >> move.x >> move.y) || stream >> extra)
        throw std::invalid_argument("Expected x,y");
    return move;
}
Rule selected_rule(const Config& config) {
    if (config.rule == 0) return Rule::freestyle;
    if (config.rule == 1) return Rule::standard;
    throw std::invalid_argument("Unsupported rule; only freestyle and standard are implemented");
}
void validate_config(const Config& config) {
    selected_rule(config);
    const auto minimum = 16 * 1024 * 1024 + (config.model ? config.model->bytes() + 8 * 1024 * 1024 : 0);
    if (config.memory > 0 && static_cast<std::uint64_t>(config.memory) < minimum)
        throw std::invalid_argument("Memory budget below model/runtime requirements");
}
void think(Position& position, const Config& config) {
    validate_config(config);
    if (position.terminal()) throw std::invalid_argument("Position is terminal");
    long long budget = std::min(config.turn_ms, config.left_ms == INT_MAX ? config.turn_ms : config.left_ms / 25);
    budget = std::clamp(budget - 10, 0LL, 10000LL);
    std::unique_ptr<Evaluator> evaluator;
    if (config.model) {
        if (!config.model->supports(position)) throw std::invalid_argument("Model does not support this board/rule");
        evaluator = std::make_unique<NnueEvaluator>(config.model);
    } else evaluator = std::make_unique<HandcraftedEvaluator>();
    std::atomic_bool cancelled{false};
    const auto result = search(position, *evaluator, {static_cast<int>(budget), 6, 0}, cancelled);
    position.play(*result.best_move);
    std::cout << result.best_move->x << ',' << result.best_move->y << std::endl;
}
Position change_rule(const Position& position, Rule rule) {
    std::vector<Stone> stones;
    for (int y = 0; y < position.size(); ++y)
        for (int x = 0; x < position.size(); ++x)
            if (position.at({x, y}) != Color::empty) stones.push_back({{x, y}, position.at({x, y})});
    return Position::from_stones(position.size(), rule, stones, position.turn());
}
}

int main(int argc, char** argv) {
    Config config;
    try {
        if (argc == 3 && std::string_view(argv[1]) == "--model") config.model = NnueModel::load_file(argv[2]);
        else if (argc != 1) throw std::invalid_argument("Usage: pbrain-gomoku [--model weights.gnn]");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    std::optional<Position> position;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        std::istringstream input(line);
        std::string command;
        input >> command;
        if (command == "END") break;
        try {
            if (command == "INFO") {
                std::string key;
                long long value;
                input >> key;
                // Unknown INFO keys are ignored, including non-numeric ones.
                if (key == "rule" || key == "timeout_turn" || key == "time_left" || key == "max_memory") {
                    if (!(input >> value)) continue;
                    if (key == "rule") config.rule = value >= 0 && value <= 15 ? static_cast<int>(value) : -1;
                    if (key == "timeout_turn") config.turn_ms = std::max(0LL, value);
                    if (key == "time_left") config.left_ms = value;
                    if (key == "max_memory") config.memory = value;
                }
                continue; // INFO must never emit a response.
            }
            if (command == "ABOUT") {
                std::cout << "name=\"Gomoku\", version=\"0.1.0\", author=\"Gomoku project\"" << std::endl;
                continue;
            }
            if (command == "START") {
                int size = 0;
                std::string extra;
                if (!(input >> size) || input >> extra) throw std::invalid_argument("Expected board size");
                position.reset();
                position.emplace(size, selected_rule(config));
                std::cout << "OK" << std::endl;
                continue;
            }
            if (command == "RESTART") {
                if (!position) throw std::invalid_argument("START required");
                position.emplace(position->size(), selected_rule(config));
                std::cout << "OK" << std::endl;
                continue;
            }
            if (command == "BOARD") {
                // Consume the entire block even when a row is bad, preserving framing.
                std::vector<std::pair<Move, int>> entries;
                bool valid = true, done = false;
                int own = 0, opponent = 0;
                while (std::getline(std::cin, line)) {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    if (line == "DONE") { done = true; break; }
                    std::replace(line.begin(), line.end(), ',', ' ');
                    std::istringstream row(line);
                    Move move{};
                    int field = 0;
                    std::string extra;
                    if (!(row >> move.x >> move.y >> field) || row >> extra ||
                        (field != 1 && field != 2) || entries.size() >= 400) { valid = false; continue; }
                    entries.push_back({move, field});
                    own += field == 1;
                    opponent += field == 2;
                }
                if (!done) break;
                if (!position || !valid || !(own == opponent || own + 1 == opponent))
                    throw std::invalid_argument("Invalid BOARD position");
                validate_config(config);
                const auto ours = own == opponent ? Color::black : Color::white;
                std::vector<Stone> stones;
                for (const auto& [move, field] : entries)
                    stones.push_back({move, field == 1 ? ours : opposite(ours)});
                auto replacement = Position::from_stones(position->size(), selected_rule(config), stones, ours);
                position = std::move(replacement);
                think(*position, config);
                continue;
            }
            if (command == "TURN" || command == "BEGIN") {
                if (!position) throw std::invalid_argument("START required");
                validate_config(config);
                if (position->rule() != selected_rule(config))
                    position = change_rule(*position, selected_rule(config));
                if (command == "TURN") {
                    std::string coordinates;
                    std::getline(input, coordinates);
                    position->play(coordinate(coordinates));
                } else if (position->ply() != 0) {
                    throw std::invalid_argument("BEGIN requires empty board");
                }
                think(*position, config);
                continue;
            }
            std::cout << "UNKNOWN " << command << std::endl;
        } catch (const std::exception& error) {
            std::cout << "ERROR " << error.what() << std::endl;
        }
    }
}
