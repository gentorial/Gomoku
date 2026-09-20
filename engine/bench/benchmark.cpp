#include "codec.h"
#include "gomoku/profile.h"
#include <fstream>
#include <iostream>
#include <string>
#ifdef __EMSCRIPTEN__
#include <emscripten/emscripten.h>
#endif

using namespace gomoku;
using namespace gomoku::wire;
namespace {
std::shared_ptr<const NnueModel> model;
Position position_from_case(const json& value) {
    GOMOKU_SCOPE(position);
    if (!value.contains("board")) return position_from(value);
    const int size = integer(value, "size", 15, 20);
    const auto& board = value.at("board");
    if (!board.is_array() || board.size() != static_cast<std::size_t>(size))
        throw std::invalid_argument("Invalid benchmark board");
    std::vector<Stone> stones;
    for (int y = 0; y < size; ++y) {
        if (!board[y].is_array() || board[y].size() != static_cast<std::size_t>(size))
            throw std::invalid_argument("Invalid benchmark row");
        for (int x = 0; x < size; ++x) {
            const auto& cell = board[y][x];
            if (!cell.is_number_integer() || cell < 0 || cell > 2)
                throw std::invalid_argument("Invalid benchmark stone");
            const int color = cell.get<int>();
            if (color) stones.push_back({{x, y}, static_cast<Color>(color)});
        }
    }
    return Position::from_stones(size, parse_rule(value.at("rule").get<std::string>()),
        stones, static_cast<Color>(integer(value, "toMove", 1, 2)));
}

json benchmark(const json& request) {
    if (!model) throw std::invalid_argument("Load the model before benchmarking");
    const int depth = integer(request, "maxDepth", 1, 12);
    const auto max_nodes = request.value("maxNodes", std::uint64_t(0));
    const int time_ms = request.value("timeMs", 600000);
    if (time_ms < 0 || time_ms > 600000) throw std::invalid_argument("Invalid benchmark time limit");
    const std::atomic_bool cancelled{false};
    profile::Recorder recorder;
    profile::Session session(recorder);
    SearchResult result;
    json iterations = json::array();
    const bool observe = request.value("observe", false);
    int plies = 0;
    const auto start = profile::Clock::now();
    {
        GOMOKU_SCOPE(total);
        auto position = position_from_case(request.at("position"));
        plies = position.ply();
        NnueEvaluator evaluator(model);
        const SearchObserver observer = observe ? SearchObserver([&](const SearchResult& partial) {
            iterations.push_back({{"depth", partial.depth}, {"nodes", partial.nodes},
                {"wallMs", profile::milliseconds(profile::Clock::now() - start)}});
        }) : SearchObserver{};
        SearchOptions options;
        options.transpositions = request.value("tt", true);
        options.pvs = request.value("pvs", true);
        result = search(position, evaluator, {time_ms, depth, max_nodes}, cancelled, observer, options);
    }
    const double elapsed = profile::milliseconds(profile::Clock::now() - start);
    json phases = json::array();
    for (std::size_t i = 0; i < profile::names.size(); ++i) {
        const auto& event = recorder.events[i];
        if (event.calls) phases.push_back({{"name", profile::names[i]}, {"calls", event.calls},
            {"inclusiveMs", event.inclusive_ms}, {"exclusiveMs", event.exclusive_ms}});
    }
    return {{"profiled", profile::enabled}, {"wallMs", elapsed}, {"plies", plies},
        {"result", analysis_json(result, "line11-nnue-v1")}, {"phases", phases}, {"iterations", iterations},
        {"stats", {{"ttProbes", result.stats.tt_probes}, {"ttHits", result.stats.tt_hits},
            {"ttCutoffs", result.stats.tt_cutoffs}, {"evaluatorPushes", result.stats.evaluator_pushes},
            {"pvsResearches", result.stats.pvs_researches}, {"preferredCutoffs", result.stats.preferred_cutoffs}}}};
}
}

#ifdef __EMSCRIPTEN__
extern "C" EMSCRIPTEN_KEEPALIVE const char* gomoku_bench_load_model(const std::uint8_t* bytes, std::size_t size) {
    static std::string output;
    try {
        if (!bytes || size == 0) throw std::invalid_argument("Missing NNUE bytes");
        model = NnueModel::load({bytes, size});
        output.clear();
    } catch (const std::exception& error) { output = error.what(); }
    return output.c_str();
}
extern "C" EMSCRIPTEN_KEEPALIVE const char* gomoku_bench(const char* input) {
    static std::string output;
    try { output = benchmark(json::parse(input)).dump(); }
    catch (const std::exception& error) { output = json{{"error", error.what()}}.dump(); }
    return output.c_str();
}
#else
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::invalid_argument("Usage: gomoku-bench weights.gnn (JSON Lines on stdin)");
        const auto start = profile::Clock::now();
        model = NnueModel::load_file(argv[1]);
        std::cout << json{{"ready", true}, {"profiled", profile::enabled},
            {"modelLoadMs", profile::milliseconds(profile::Clock::now() - start)}}.dump() << std::endl;
        std::string line;
        while (std::getline(std::cin, line)) {
            try { std::cout << benchmark(json::parse(line)).dump() << std::endl; }
            catch (const std::exception& error) { std::cout << json{{"error", error.what()}}.dump() << std::endl; }
        }
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
#endif
