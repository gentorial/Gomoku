#pragma once

// Optional diagnostic instrumentation. Production builds compile every scope out.
// Each event records inclusive time and exclusive time (children subtracted).
#include <array>
#include <chrono>
#include <cstdint>
#include <string_view>

namespace gomoku::profile {
enum class Phase {
    total, position, evaluator, search, checks, candidates, ranking, sorting,
    play, undo, reset, reset_features, reset_spatial, push, save,
    features, spatial, pop, value, value_input, value_hidden, value_activation,
    value_output, score, policy, policy_input, policy_global, policy_pair,
    policy_local, policy_activation, policy_output, count
};
constexpr std::array<std::string_view, static_cast<int>(Phase::count)> names{
    "total", "setup.position", "setup.evaluator", "search", "search.checks",
    "board.candidates", "search.ranking", "search.sorting", "board.play", "board.undo",
    "nnue.reset", "nnue.reset.features", "nnue.reset.spatial", "nnue.push", "nnue.save",
    "nnue.features", "nnue.spatial", "nnue.pop", "nnue.value", "nnue.value.input",
    "nnue.value.hidden", "nnue.value.activation", "nnue.value.output", "nnue.score",
    "nnue.policy", "nnue.policy.input", "nnue.policy.global", "nnue.policy.pair",
    "nnue.policy.local", "nnue.policy.activation", "nnue.policy.output"
};
using Clock = std::chrono::steady_clock;
inline double milliseconds(Clock::duration elapsed) {
    return std::chrono::duration<double, std::milli>(elapsed).count();
}
struct Event {
    std::uint64_t calls = 0;
    double inclusive_ms = 0;
    double exclusive_ms = 0;
};
struct Recorder {
    std::array<Event, names.size()> events{};
};

#ifdef GOMOKU_PROFILE
inline constexpr bool enabled = true;
class Scope;
inline thread_local Recorder* current = nullptr;
inline thread_local Scope* top = nullptr;
class Session {
    Recorder* previous_ = current;
    Scope* previous_top_ = top;
public:
    explicit Session(Recorder& recorder) { current = &recorder; top = nullptr; }
    ~Session() { current = previous_; top = previous_top_; }
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
};
class Scope {
    Recorder* recorder_ = current;
    Scope* parent_ = top;
    Phase phase_;
    Clock::time_point start_;
    double children_ms_ = 0;
public:
    explicit Scope(Phase phase) : phase_(phase) {
        if (recorder_) { start_ = Clock::now(); top = this; }
    }
    ~Scope() {
        if (!recorder_) return;
        const double elapsed = milliseconds(Clock::now() - start_);
        auto& event = recorder_->events[static_cast<int>(phase_)];
        ++event.calls;
        event.inclusive_ms += elapsed;
        event.exclusive_ms += elapsed - children_ms_;
        if (parent_) parent_->children_ms_ += elapsed;
        top = parent_;
    }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
};
#define GOMOKU_PROFILE_JOIN_(a, b) a##b
#define GOMOKU_PROFILE_JOIN(a, b) GOMOKU_PROFILE_JOIN_(a, b)
#define GOMOKU_SCOPE(phase) ::gomoku::profile::Scope GOMOKU_PROFILE_JOIN(profile_scope_, __LINE__)(::gomoku::profile::Phase::phase)
#define GOMOKU_SCOPE_PHASE(phase) ::gomoku::profile::Scope GOMOKU_PROFILE_JOIN(profile_scope_, __LINE__)(phase)
#else
inline constexpr bool enabled = false;
class Session {
public:
    explicit Session(Recorder&) {}
};
#define GOMOKU_SCOPE(phase) ((void)0)
#define GOMOKU_SCOPE_PHASE(phase) ((void)0)
#endif
} // namespace gomoku::profile
