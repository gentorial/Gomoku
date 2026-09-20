#include "codec.h"
#include <emscripten/emscripten.h>
#include <cstring>

namespace {
std::shared_ptr<const gomoku::NnueModel> model;
std::string model_error;
}
// Bytes are copied into validated, immutable tensors. The caller owns/frees
// the input allocation. A failed replacement preserves the previous model.
extern "C" EMSCRIPTEN_KEEPALIVE const char* gomoku_load_model(const std::uint8_t* data, std::size_t size) {
    try {
        if (!data || size == 0) throw std::invalid_argument("Missing NNUE bytes");
        auto next = gomoku::NnueModel::load({data, size});
        model = std::move(next);
        model_error.clear();
    } catch (const std::exception& error) {
        model_error = error.what();
    }
    return model_error.c_str();
}

// Diagnostic ABI for independent Python/native/WASM numerical parity checks.
// Game clients use gomoku_request; this function never searches or modifies a game.
extern "C" EMSCRIPTEN_KEEPALIVE const char* gomoku_nnue_predict(const char* input) {
    using namespace gomoku;
    using namespace gomoku::wire;
    static std::string output;
    try {
        if (!input || std::strlen(input) > 65536) throw std::invalid_argument("Invalid diagnostic request");
        const auto request = json::parse(input);
        const int size = integer(request, "size", 15, 20);
        const auto& board = request.at("board");
        if (!board.is_array() || board.size() != static_cast<std::size_t>(size)) throw std::invalid_argument("Invalid board");
        std::vector<Stone> stones;
        std::vector<Move> moves;
        for (int y = 0; y < size; ++y) {
            if (!board[y].is_array() || board[y].size() != static_cast<std::size_t>(size)) throw std::invalid_argument("Invalid row");
            for (int x = 0; x < size; ++x) {
                if (!board[y][x].is_number_integer() || board[y][x] < 0 || board[y][x] > 2) throw std::invalid_argument("Invalid cell");
                const auto color = static_cast<Color>(board[y][x].get<int>());
                if (color != Color::empty) stones.push_back({{x, y}, color});
                moves.push_back({x, y});
            }
        }
        auto position = Position::from_stones(size, parse_rule(request.at("rule").get<std::string>()),
            stones, static_cast<Color>(integer(request, "toMove", 1, 2)));
        NnueEvaluator evaluator(model);
        evaluator.reset(position);
        output = json{{"valueLogits", evaluator.value_logits(position)},
                      {"policyLogits", evaluator.move_scores(position, moves)}}.dump();
    } catch (const std::exception& error) {
        output = json{{"error", error.what()}}.dump();
    }
    return output.c_str();
}

// A module lives in one dedicated Web Worker. Returned storage is valid until
// the next call; ccall copies it into a JavaScript string before that happens.
extern "C" EMSCRIPTEN_KEEPALIVE const char* gomoku_request(const char* input) {
    using namespace gomoku::wire;
    static std::string output;
    std::string id = "invalid-request";
    json response;
    try {
        if (!input || std::strlen(input) > 65536)
            throw std::invalid_argument("Request exceeds 64 KiB");
        const auto request = json::parse(input);
        id = request.at("id").get<std::string>();
        if (id.empty() || id.size() > 128) throw std::invalid_argument("Invalid request id");
        integer(request, "v", 1, 1);
        response = {{"v", 1}, {"id", id}, {"ok", true}, {"result", dispatch(request, model)}};
    } catch (const std::exception& error) {
        response = {{"v", 1}, {"id", id}, {"ok", false},
                    {"error", {{"code", "INVALID_REQUEST"}, {"message", error.what()}}}};
    }
    output = response.dump();
    return output.c_str();
}
