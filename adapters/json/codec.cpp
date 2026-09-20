#include "codec.h"
#include <cmath>
#include <stdexcept>

namespace gomoku::wire {
int integer(const json& object, const char* key, int minimum, int maximum) {
    const auto& value = object.at(key);
    if (!value.is_number_integer() || value < minimum || value > maximum)
        throw std::invalid_argument(std::string("Invalid integer: ") + key);
    return value.get<int>();
}
Move move_from(const json& value, int size) {
    return {integer(value, "x", 0, size - 1), integer(value, "y", 0, size - 1)};
}
json move_json(Move move) { return {{"x", move.x}, {"y", move.y}}; }
Position position_from(const json& value) {
    Position position(integer(value, "size", 15, 20), parse_rule(value.at("rule").get<std::string>()));
    const auto& moves = value.at("moves");
    if (!moves.is_array() || moves.size() > static_cast<std::size_t>(position.size() * position.size()))
        throw std::invalid_argument("Invalid move history");
    for (const auto& move : moves) position.play(move_from(move, position.size()));
    return position;
}
json position_json(const Position& position, const json& moves) {
    return {{"kind", "position"}, {"size", position.size()}, {"rule", rule_name(position.rule())},
            {"moves", moves}, {"toMove", color_name(position.turn())}, {"status", position.status()}};
}
json analysis_json(const SearchResult& result, std::string_view evaluator) {
    json pv = json::array();
    for (auto move : result.pv) pv.push_back(move_json(move));
    return {{"kind", "analysis"}, {"bestMove", result.best_move ? move_json(*result.best_move) : json(nullptr)},
            {"score", {{"value", result.score}, {"perspective", "side_to_move"},
                       {"kind", std::abs(result.score) > 90000 ? "mate" : "heuristic"}}},
            {"depth", result.depth}, {"nodes", result.nodes}, {"elapsedMs", result.elapsed_ms},
            {"pv", pv}, {"reason", result.reason}, {"evaluator", evaluator}};
}
SearchLimits limits_from(const json& request) {
    SearchLimits limits;
    const auto config = request.value("limits", json::object());
    if (config.contains("timeMs")) limits.time_ms = integer(config, "timeMs", 0, 10000);
    if (config.contains("maxDepth")) limits.max_depth = integer(config, "maxDepth", 1, 12);
    if (config.contains("maxNodes")) limits.max_nodes = integer(config, "maxNodes", 0, 10000000);
    return limits;
}
json about_json(const std::shared_ptr<const NnueModel>& model) {
    return {{"kind", "about"}, {"name", "Gomoku"}, {"version", "0.1.0"},
            {"protocolVersion", 1}, {"rules", {"freestyle", "standard"}},
            {"sizes", {15, 20}}, {"evaluator", model ? "line11-nnue-v1" : "handcrafted-v1"},
            {"nnue", model ? json{{"size", model->size()}, {"rule", rule_name(model->rule())},
                                   {"bytes", model->bytes()}} : json(nullptr)}};
}
std::unique_ptr<Evaluator> evaluator_for(const json& request, const Position& position,
                                       const std::shared_ptr<const NnueModel>& model) {
    const auto selection = request.value("evaluator", "auto");
    if (selection != "auto" && selection != "nnue" && selection != "handcrafted")
        throw std::invalid_argument("Unknown evaluator");
    if (selection == "nnue" && (!model || !model->supports(position)))
        throw std::invalid_argument("NNUE model is missing or does not support this board/rule");
    if (selection != "handcrafted" && model && model->supports(position))
        return std::make_unique<NnueEvaluator>(model);
    return std::make_unique<HandcraftedEvaluator>();
}
json dispatch(const json& request, const std::shared_ptr<const NnueModel>& model) {
    const auto method = request.at("method").get<std::string>();
    if (method == "about") return about_json(model);
    if (method != "inspect" && method != "play" && method != "analyze")
        throw std::invalid_argument("Unknown method");
    auto position = position_from(request.at("position"));
    auto moves = request.at("position").at("moves");
    if (method == "play") {
        const auto move = move_from(request.at("move"), position.size());
        position.play(move);
        moves.push_back(move_json(move));
    }
    if (method != "analyze") return position_json(position, moves);
    auto evaluator = evaluator_for(request, position, model);
    const std::atomic_bool cancelled{false};
    return analysis_json(search(position, *evaluator, limits_from(request), cancelled), evaluator->name());
}
}
