#include "gomoku/vcf.h"
#include "gomoku/search.h"
#include <iostream>
#include <random>
#include <stdexcept>

using namespace gomoku;
namespace {
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
constexpr Move dirs[]{{1,0},{0,1},{1,1},{1,-1}};
bool four_oracle(const Position& p, Move move, Color color) {
    // Independently count runs after two hypothetical stones, without the LUT.
    for (auto d : dirs) for (int offset = -4; offset <= 4; ++offset) {
        const Move reply{move.x+d.x*offset,move.y+d.y*offset};
        if (!offset || !p.contains(reply) || p.at(reply) != Color::empty) continue;
        int length = 1;
        bool touches = false;
        for (int sign : {-1,1}) {
            Move next{reply.x+sign*d.x,reply.y+sign*d.y};
            while (p.contains(next) && (next == move || p.at(next) == color)) {
                touches |= next == move;
                ++length; next.x += sign*d.x; next.y += sign*d.y;
            }
        }
        if (touches && (p.rule() == Rule::freestyle ? length >= 5 : length == 5)) return true;
    }
    return false;
}
void verify(const Position& p, const Threats& t) {
    for (Color side : {Color::black,Color::white}) {
        int wins = 0, fours = 0;
        for (int y = 0; y < p.size(); ++y) for (int x = 0; x < p.size(); ++x) {
            const Move m{x,y};
            const bool empty = p.at(m) == Color::empty;
            const bool win = empty && p.wins(m,side);
            require(t.wins(m,side) == win, "incremental win features disagree with board rules");
            wins += win;
            // Winning moves need not classify their post-terminal follow-ups.
            if (empty && !win) require(t.four(m,side) == four_oracle(p,m,side), "four feature disagrees with run-count oracle");
            fours += t.four(m,side);
        }
        require(t.winning_count(side) == wins && t.four_count(side) == fours, "threat counts disagree with features");
    }
}
Position ladder(Rule rule = Rule::freestyle) {
    return Position::from_stones(15, rule,
        std::vector<Stone>{{{5,7},Color::black},{{6,7},Color::black},{{7,7},Color::black},
            {{8,5},Color::black},{{8,6},Color::black},{{4,7},Color::white}}, Color::black);
}
void verify_proof(Position p, const VcfResult& proof) {
    require(proof.status == VcfStatus::win && !proof.pv.empty(), "expected a winning VCF proof");
    const Color attacker = p.turn();
    for (std::size_t i = 0; i < proof.pv.size(); ++i) {
        require(p.legal(proof.pv[i]), "illegal proof move");
        if (p.turn() != attacker) {
            Threats threats(p);
            require(!threats.winning_count(p.turn()), "defender could win before blocking");
            const auto forced = threats.winning_moves(attacker);
            require(!forced.empty(), "VCF defence is not forced");
            if (forced.size() > 1) require(i+2 == proof.pv.size(), "double threat must end the proof");
            else require(forced.front() == proof.pv[i], "wrong sole defence");
        }
        p.play(proof.pv[i]);
    }
    require(p.winner() == attacker, "proof must finish in a real five");
}
struct Flat : Evaluator {
    std::vector<std::uint64_t> stack;
    std::string_view name() const override { return "flat-vcf-test"; }
    void reset(const Position& p) override { stack = {p.hash()}; }
    void push(const Position& p, Move) override { stack.push_back(p.hash()); }
    void pop() override { stack.pop_back(); }
    int evaluate(const Position& p) const override {
        require(stack.back() == p.hash(), "VCF corrupted the NNUE accumulator order"); return 0;
    }
};
}
int main() {
    try {
        std::mt19937 random(5517);
        for (Rule rule : {Rule::freestyle,Rule::standard}) for (int size : {15,20}) {
            Position p(size,rule); Threats t(p); std::vector<Move> history;
            for (int n = 0; n < 72 && !p.terminal(); ++n) {
                auto moves = p.candidates(); auto move = moves[random()%moves.size()];
                const auto color = p.turn(); p.play(move); t.play(move,color); history.push_back(move);
                if (n%12 == 0) verify(p,t);
            }
            while (!history.empty()) {
                t.undo(history.back()); p.undo(); history.pop_back();
                if (history.size()%12 == 0) verify(p,t);
            }
            require(p.ply() == 0, "undo must restore empty board");
        }
        for (Rule rule : {Rule::freestyle,Rule::standard}) {
            auto p = ladder(rule); Threats t(p); const auto hash = p.hash();
            const auto proof = solve_vcf(p,t);
            verify_proof(p,proof);
            require(proof.pv.size() == 5 && p.hash() == hash, "five-ply ladder/state restoration");
            verify(p,t);
            require(solve_vcf(p,t,{3,1024}).status == VcfStatus::unknown, "depth exhaustion must be unknown");
            require(solve_vcf(p,t,{32,0}).status == VcfStatus::unknown, "node exhaustion must be unknown");
            int visits = 0;
            try { solve_vcf(p,t,{},[&] { if (++visits == 3) throw std::runtime_error("cancel"); }); }
            catch (const std::runtime_error&) {}
            require(visits == 3 && p.hash() == hash, "interruption must unwind the proof board");
            verify(p,t);
            Flat evaluator; std::atomic_bool cancel{false};
            const auto result = search(p,evaluator,{2000,1,0},cancel);
            require(result.score == 99995 && result.stats.vcf_wins, "depth-one search must read the five-ply VCF");
            verify_proof(p,{VcfStatus::win,result.pv,0});
            require(evaluator.stack.size() == 1 && evaluator.stack.back() == hash, "VCF must restore evaluator");
            const auto stopped = search(p,evaluator,{2000,8,4},cancel);
            require(stopped.reason == "limit" && stopped.nodes <= 4 && evaluator.stack.size() == 1,
                    "proof work must obey the parent node budget");
        }
        // Counter-five always takes precedence over starting a forcing attack.
        auto p = Position::from_stones(15,Rule::freestyle,
            std::vector<Stone>{{{5,7},Color::black},{{6,7},Color::black},{{7,7},Color::black},
                {{3,3},Color::white},{{4,3},Color::white},{{5,3},Color::white},{{6,3},Color::white}}, Color::black);
        Threats threats(p);
        require(solve_vcf(p,threats).status == VcfStatus::no_win, "counter-five invalidates attack");
        // Filling the middle of six is a win only in freestyle.
        for (Rule rule : {Rule::freestyle,Rule::standard}) {
            const auto line = Position::from_stones(15,rule,
                std::vector<Stone>{{{4,7},Color::black},{{5,7},Color::black},{{6,7},Color::black},
                    {{8,7},Color::black},{{9,7},Color::black}}, Color::black);
            const Threats features(line);
            require(features.wins({7,7},Color::black) == (rule == Rule::freestyle),
                    "standard rules must reject an overline through the candidate");
            verify(line,features);
        }
        // User record: after white F11, black must answer G10; the resulting
        // threats on H11 and M10 both have a continuous-four proof.
        Position record;
        for (Move move : std::vector<Move>{{7,7},{7,6},{8,8},{6,6},{5,6},{8,5},
                {6,7},{9,6},{10,7},{8,7},{10,5},{8,6},{10,6},{10,8},{7,5},{9,8},
                {10,9},{9,9},{10,3},{10,4},{9,7},{8,4},{8,3},{5,4}}) record.play(move);
        Flat evaluator; std::atomic_bool cancel{false};
        const auto answer = search(record,evaluator,{2000,2,0},cancel);
        require(answer.best_move == Move{6,5} && answer.score > 99000,
                "the sole G10 defence must also reveal its winning continuation");
        auto finish = record;
        for (Move move : answer.pv) finish.play(move);
        require(finish.winner() == Color::black, "record PV must finish in an actual black five");
        record.play({6,5});
        for (Move white : {Move{8,10},Move{7,4}}) {
            record.play(white);
            Threats features(record);
            verify_proof(record,solve_vcf(record,features));
            record.undo();
        }
        std::cout << "Threat make/unmake, exact-five rules, VCF proofs, counters and budget unwind passed\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
