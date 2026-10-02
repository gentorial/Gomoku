#pragma once
#include "codec.h"
#include <functional>

namespace gomoku::wire {
// Adapter boundary only: no instrumentation inside neural-network kernels.
using DiagnosticSink = std::function<void(const json&)>;
void set_diagnostic_sink(DiagnosticSink sink, bool debug = false);
void diagnostic(std::string_view event, json fields = json::object()) noexcept;
SearchOptions logged_search_options(const json& request, const Position& position);
}
