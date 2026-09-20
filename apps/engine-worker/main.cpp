#include "codec.h"
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

using namespace gomoku;
using namespace gomoku::wire;
namespace {
std::mutex output_mutex;
void emit(const std::string& id, const json& result, bool ok = true) {
    const std::lock_guard lock(output_mutex);
    json response{{"v", 1}, {"id", id}, {"ok", ok}};
    response[ok ? "result" : "error"] = result;
    std::cout << response.dump() << std::endl;
}
}

int main(int argc, char** argv) {
    std::shared_ptr<const NnueModel> model;
    try {
        if (argc == 3 && std::string_view(argv[1]) == "--model") model = NnueModel::load_file(argv[2]);
        else if (argc != 1) throw std::invalid_argument("Usage: gomoku-worker [--model weights.gnn]");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    std::atomic_bool cancelled{false}, busy{false};
    std::thread analysis;
    std::string active_id;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::string id = "invalid-request";
        try {
            if (line.size() > 65536) throw std::invalid_argument("Request exceeds 64 KiB");
            const auto request = json::parse(line);
            id = request.at("id").get<std::string>();
            if (id.empty() || id.size() > 128) throw std::invalid_argument("Invalid request id");
            integer(request, "v", 1, 1);
            const auto method = request.at("method").get<std::string>();
            if (method == "stop") {
                const auto target = request.at("targetId").get<std::string>();
                const bool matched = busy.load() && target == active_id;
                if (matched) cancelled = true;
                emit(id, {{"kind", "stopped"}, {"matched", matched}});
                continue;
            }
            if (busy.load()) throw std::invalid_argument("Worker is busy");
            if (analysis.joinable()) analysis.join();
            if (method != "analyze") {
                emit(id, dispatch(request, model));
                continue;
            }
            const auto position = position_from(request.at("position"));
            const auto limits = limits_from(request);
            auto evaluator = evaluator_for(request, position, model);
            active_id = id;
            cancelled = false;
            busy = true;
            analysis = std::thread([&, position, limits, id, evaluator = std::move(evaluator)] {
                try {
                    const auto result = search(position, *evaluator, limits, cancelled);
                    busy = false;
                    emit(id, analysis_json(result, evaluator->name()));
                } catch (const std::exception& error) {
                    busy = false;
                    emit(id, {{"code", "ENGINE_ERROR"}, {"message", error.what()}}, false);
                }
            });
        } catch (const std::exception& error) {
            emit(id, {{"code", "INVALID_REQUEST"}, {"message", error.what()}}, false);
        }
    }
    cancelled = true;
    if (analysis.joinable()) analysis.join();
}
