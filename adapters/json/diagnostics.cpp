#include "diagnostics.h"
#include <chrono>
#include <utility>

namespace gomoku::wire {
namespace {
DiagnosticSink sink;
bool detailed = false;
}
void set_diagnostic_sink(DiagnosticSink next, bool debug) {
    sink = std::move(next); detailed = debug;
}
void diagnostic(std::string_view event, json fields) noexcept {
    if (!sink) return;
    try {
        fields["event"] = event;
        fields["component"] = "engine";
        fields["timestampMs"] = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        fields["level"] = event.ends_with("failed") ? "error" :
            event.starts_with("search.") && event != "search.start" && event != "search.iteration" &&
            event != "search.end" && event != "search.cancel_requested" ? "debug" : "info";
        sink(fields);
    } catch (...) { /* Diagnostics must not change protocol or search behavior. */ }
}
SearchOptions logged_search_options(const json& request, const Position& position) {
    SearchOptions options;
    if (!sink) return options;
    options.debug_log = detailed;
    const auto limits = limits_from(request);
    const json context{{"requestId", request.at("id")}, {"rootHash", std::to_string(position.hash())},
        {"size", position.size()}, {"rule", rule_name(position.rule())},
        {"toMove", color_name(position.turn())}, {"plyCount", request.at("position").at("moves").size()},
        {"limits", {{"timeMs", limits.time_ms}, {"maxDepth", limits.max_depth}, {"maxNodes", limits.max_nodes}}},
        {"tt", options.transpositions}, {"pvs", options.pvs}, {"vcf", options.vcf},
        {"tableEntries", options.table_entries}, {"vcfMaxPlies", options.vcf_max_plies},
        {"vcfNodeLimit", options.vcf_node_limit}};
    options.log = [context, root = request.at("position")](const SearchEvent& event) {
        json fields = context;
        if (event.event == "search.start") fields["position"] = root;
        fields["positionHash"] = event.position_hash ? json(std::to_string(*event.position_hash)) : context.at("rootHash");
        fields.update({{"depth", event.depth}, {"ply", event.ply},
            {"attemptedDepth", event.attempted_depth}, {"completedDepth", event.completed_depth},
            {"score", event.score}, {"alpha", event.alpha}, {"beta", event.beta},
            {"nodes", event.nodes}, {"elapsedMs", event.elapsed_ms},
            {"nodeDelta", event.node_delta}, {"elapsedDeltaMs", event.elapsed_delta_ms},
            {"candidateCount", event.candidate_count}, {"suppressedEvents", event.suppressed},
            {"fallbackUsed", event.event == "search.end" && event.completed_depth == 0 && event.move.has_value()},
            {"move", event.move ? move_json(*event.move) : json(nullptr)}});
        if (!event.reason.empty()) fields["reason"] = event.reason;
        fields["moves"] = json::array();
        for (const auto move : event.moves) fields["moves"].push_back(move_json(move));
        fields["winningMoves"] = json::array(); fields["blockingMoves"] = json::array();
        for (const auto move : event.wins) fields["winningMoves"].push_back(move_json(move));
        for (const auto move : event.blocks) fields["blockingMoves"].push_back(move_json(move));
        const auto& stats = event.stats;
        fields["stats"] = {{"ttProbes", stats.tt_probes}, {"ttHits", stats.tt_hits},
            {"ttCutoffs", stats.tt_cutoffs}, {"betaCutoffs", stats.beta_cutoffs},
            {"preferredCutoffs", stats.preferred_cutoffs}, {"pvsResearches", stats.pvs_researches},
            {"vcfNodes", stats.vcf_nodes}, {"vcfWins", stats.vcf_wins}, {"vcfUnknown", stats.vcf_unknown},
            {"vcfCalls", stats.vcf_calls}, {"vcfNoWin", stats.vcf_no_win}, {"vcfElapsedMs", stats.vcf_elapsed_ms},
            {"forcedReplies", stats.forced_replies}, {"immediateWins", stats.immediate_wins},
            {"doubleThreats", stats.double_threats}};
        diagnostic(event.event, std::move(fields));
    };
    return options;
}
}
