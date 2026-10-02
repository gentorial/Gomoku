#include "codec.h"
#include "diagnostics.h"
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

using namespace gomoku;
using namespace gomoku::wire;
namespace {
std::mutex output_mutex;
std::mutex log_mutex;
void write_log(const json& record) {
    const std::lock_guard lock(log_mutex);
    const auto now = std::time(nullptr);
    const auto date = *std::gmtime(&now);
    std::ostringstream name;
    name << "engine-" << std::put_time(&date, "%Y-%m-%d") << ".jsonl";
    static std::ofstream file;
    static std::string day;
    if (day != name.str() || !file.is_open()) {
        file.close(); file.clear();
        const auto configured = std::getenv("GOMOKU_LOG_DIR");
        const auto directory = std::filesystem::path(configured ? configured : GOMOKU_DEFAULT_LOG_DIR);
        std::error_code error;
        std::filesystem::create_directories(directory, error);
        file.open(directory / name.str(), std::ios::app);
        day = name.str();
    }
    const auto line = record.dump();
    std::cerr << line << '\n';
    if (file) { file << line << '\n'; file.flush(); }
}
void emit(const std::string& id, const json& result, bool ok = true) {
    const std::lock_guard lock(output_mutex);
    json response{{"v", 1}, {"id", id}, {"ok", ok}};
    response[ok ? "result" : "error"] = result;
    std::cout << response.dump() << std::endl;
}
}

int main(int argc, char** argv) {
    const auto configured_level = std::getenv("GOMOKU_LOG_LEVEL");
    const std::string_view level = configured_level ? configured_level : "info";
    if (level != "off") set_diagnostic_sink(write_log, level == "debug");
    diagnostic("worker.start", {{"argc", argc}});
    std::shared_ptr<const NnueModel> model;
    try {
        if (argc == 3 && std::string_view(argv[1]) == "--model") {
            diagnostic("model.load_start", {{"path", argv[2]}});
            model = NnueModel::load_file(argv[2]);
            diagnostic("model.ready", {{"bytes", model->bytes()}, {"size", model->size()}, {"rule", rule_name(model->rule())}});
        }
        else if (argc != 1) throw std::invalid_argument("Usage: gomoku-worker [--model weights.gnn]");
    } catch (const std::exception& error) {
        diagnostic("worker.start_failed", {{"message", error.what()}});
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
            diagnostic("worker.request", {{"requestId", id}, {"method", method}});
            if (method == "stop") {
                const auto target = request.at("targetId").get<std::string>();
                const bool matched = busy.load() && target == active_id;
                if (matched) cancelled = true;
                diagnostic("search.cancel_requested", {{"requestId", id}, {"targetId", target}, {"matched", matched}});
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
            const auto options = logged_search_options(request, position);
            active_id = id;
            cancelled = false;
            busy = true;
            analysis = std::thread([&, position, limits, options, id, evaluator = std::move(evaluator)] {
                try {
                    const auto result = search(position, *evaluator, limits, cancelled, {}, options);
                    busy = false;
                    emit(id, analysis_json(result, evaluator->name()));
                } catch (const std::exception& error) {
                    diagnostic("search.failed", {{"requestId", id}, {"message", error.what()}});
                    busy = false;
                    emit(id, {{"code", "ENGINE_ERROR"}, {"message", error.what()}}, false);
                }
            });
        } catch (const std::exception& error) {
            diagnostic("worker.request_failed", {{"requestId", id}, {"message", error.what()}});
            emit(id, {{"code", "INVALID_REQUEST"}, {"message", error.what()}}, false);
        }
    }
    cancelled = true;
    if (analysis.joinable()) analysis.join();
    diagnostic("worker.closed");
}
