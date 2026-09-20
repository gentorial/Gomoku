#include "gomoku/nnue.h"
#include "gomoku/profile.h"
#include "nnue_kernels.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <numeric>
#include <stdexcept>

namespace gomoku {
namespace {
constexpr int patterns = 397488;
constexpr std::array<Move, 4> directions{{{1, 0}, {0, 1}, {1, 1}, {1, -1}}};
int aligned(int features) { return (features + 2 + 15) / 16 * 16; }
std::int64_t rounded(std::int64_t value, int denominator) {
    // C++ divides toward zero; the reference uses floor followed by ties-to-even.
    auto quotient = value / denominator;
    auto remainder = value % denominator;
    if (remainder < 0) { --quotient; remainder += denominator; }
    return quotient + (2 * remainder > denominator ||
        (2 * remainder == denominator && quotient % 2 != 0));
}
std::int16_t activate(std::int64_t value) {
    return static_cast<std::int16_t>(std::clamp<std::int64_t>(rounded(value, 1024), 0, 256));
}
struct Reader {
    std::span<const std::uint8_t> bytes;
    std::size_t offset = 0;
    std::span<const std::uint8_t> take(std::size_t count) {
        if (count > bytes.size() - offset) throw std::invalid_argument("Truncated NNUE model");
        auto result = bytes.subspan(offset, count);
        offset += count;
        return result;
    }
    std::uint32_t u32() {
        auto b = take(4);
        return b[0] | (std::uint32_t(b[1]) << 8) | (std::uint32_t(b[2]) << 16) | (std::uint32_t(b[3]) << 24);
    }
    void expect(std::uint32_t value) {
        if (u32() != value) throw std::invalid_argument("NNUE header or tensor shape mismatch");
    }
    int width() {
        auto value = u32();
        if (value < 1 || value > 1024) throw std::invalid_argument("Unsupported NNUE width");
        return static_cast<int>(value);
    }
};
}

std::shared_ptr<const NnueModel> NnueModel::load(std::span<const std::uint8_t> bytes) {
    if (bytes.size() > max_bytes) throw std::invalid_argument("NNUE model exceeds 512 MiB runtime limit");
    Reader reader{bytes};
    const auto magic = reader.take(8);
    if (std::memcmp(magic.data(), "GMLINE1\0", 8) != 0) throw std::invalid_argument("Invalid NNUE magic");
    reader.expect(1); reader.expect(1);
    reader.width(); // Mapping MLP was folded into the codebooks during export.
    auto model = std::shared_ptr<NnueModel>(new NnueModel);
    const int c = model->channels_ = reader.width();
    const int v = model->value_hidden_ = reader.width();
    const int p = model->policy_hidden_ = reader.width();
    reader.expect(256); reader.expect(1024);
    model->size_ = static_cast<int>(reader.u32());
    const auto rule = reader.u32();
    if ((model->size_ != 15 && model->size_ != 20) || rule > 1)
        throw std::invalid_argument("Unsupported NNUE board or rule");
    model->rule_ = rule == 0 ? Rule::freestyle : Rule::standard;
    reader.expect(14); reader.expect(patterns);
    const std::array<std::string_view, 14> names{
        "codebook.hv", "codebook.diag", "spatial.weight", "spatial.bias",
        "value_hidden.weight", "value_hidden.bias", "value_out.weight", "value_out.bias",
        "policy_local.weight", "policy_local.bias", "policy_global.weight", "policy_global.bias",
        "policy_out.weight", "policy_out.bias"};
    const std::array<std::vector<int>, 14> shapes{{
        {patterns,c}, {patterns,c}, {c,1,3,3}, {c}, {v,aligned(20*c)}, {v}, {3,v}, {3},
        {p,2*c}, {p}, {p,aligned(2*c)}, {p}, {1,p}, {1}}};
    // Validate the entire expected length before allocating any tensor storage.
    std::size_t expected = 56 + 14 * 56;
    for (const auto& shape : shapes) {
        std::size_t count = 1;
        for (int dimension : shape) count *= dimension;
        expected += count * 2;
    }
    if (expected != bytes.size()) throw std::invalid_argument("NNUE file size mismatch");
    for (std::size_t i = 0; i < names.size(); ++i) {
        std::array<char, 32> name{};
        std::memcpy(name.data(), names[i].data(), names[i].size());
        const auto actual = reader.take(32);
        if (std::memcmp(actual.data(), name.data(), 32) != 0)
            throw std::invalid_argument("NNUE tensor name mismatch");
        reader.expect(static_cast<std::uint32_t>(shapes[i].size()));
        reader.expect(i < 2 ? 256 : 1024);
        std::size_t count = 1;
        for (std::size_t d = 0; d < 4; ++d) {
            const int dimension = d < shapes[i].size() ? shapes[i][d] : 0;
            reader.expect(dimension);
            if (dimension) count *= dimension;
        }
        const auto data = reader.take(count * 2);
        auto& tensor = model->tensors_[i];
        tensor.resize(count);
        const int bound = i < 2 ? 256 : 8192;
        for (std::size_t k = 0; k < count; ++k) {
            const unsigned raw = data[2*k] | (unsigned(data[2*k+1]) << 8);
            const int value = raw < 32768 ? static_cast<int>(raw) : static_cast<int>(raw) - 65536;
            if (value < -bound || value > bound) throw std::invalid_argument("NNUE tensor value out of range");
            tensor[k] = static_cast<std::int16_t>(value);
        }
    }
    model->bytes_ = bytes.size();
    return model;
}
std::shared_ptr<const NnueModel> NnueModel::load_file(const std::string& path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) throw std::invalid_argument("Cannot open NNUE model: " + path);
    const auto length = file.tellg();
    if (length < 0 || static_cast<std::uint64_t>(length) > max_bytes)
        throw std::invalid_argument("Invalid NNUE file length");
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(length));
    file.seekg(0);
    if (!file.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size())))
        throw std::invalid_argument("Cannot read NNUE model");
    return load(bytes);
}
bool NnueModel::supports(const Position& position) const {
    return position.size() == size_ && position.rule() == rule_;
}

struct NnueEvaluator::State {
    std::shared_ptr<const NnueModel> model;
    struct Line { int offset; std::vector<int> cells; };
    struct LineChange { int point, direction, power; };
    struct Neighbor { int point, kernel; };
    struct Affected { std::vector<int> merged, spatial; std::vector<LineChange> lines; };
    struct Frame {
        int point;
        std::uint64_t hash;
        std::vector<std::int16_t> merged, spatial;
        std::vector<std::int32_t> preactivation, pools;
    };
    int size, c, count, depth = 0;
    Color turn = Color::empty;
    std::uint64_t hash = 0;
    std::vector<std::array<Line, 4>> lines;
    std::vector<Affected> affected;
    std::vector<std::vector<Neighbor>> fanouts;
    std::vector<std::array<std::array<int, 4>, 2>> pattern_ids;
    std::vector<int> regions;
    std::array<int, 10> areas{};
    std::vector<std::int16_t> merged, spatial, deltas, spatial_weights;
    std::vector<std::int32_t> preactivation, pools;
    std::vector<bool> changed;
    std::vector<Frame> frames;
    // Scratch buffers belong to this evaluator/search and are reused at every node.
    std::vector<std::int16_t> value_input, value_activated, policy_input, paired, policy_activated;
    std::vector<std::int64_t> value_hidden, policy_global, policy_local;
    std::array<std::int64_t, 3> value_output{};
    std::array<std::int64_t, 1> policy_output{};

    explicit State(std::shared_ptr<const NnueModel> weights) : model(std::move(weights)),
        size(model->size_), c(model->channels_), count(size*size),
        lines(count), affected(count), fanouts(count), pattern_ids(count), regions(count),
        merged(2*count*c), spatial(2*count*c), deltas(2*count*c), spatial_weights(9*c),
        preactivation(2*count*c), pools(20*c), changed(2*count),
        value_input(aligned(20*c)), value_activated(model->value_hidden_),
        policy_input(aligned(2*c)), paired(2*c), policy_activated(model->policy_hidden_),
        value_hidden(model->value_hidden_), policy_global(model->policy_hidden_), policy_local(model->policy_hidden_) {
        for (int channel = 0; channel < c; ++channel) for (int k = 0; k < 9; ++k)
            spatial_weights[k*c+channel] = model->tensors_[2][channel*9+k];
        int offsets[6][6], offset = 0;
        for (int left = 0; left <= 5; ++left)
            for (int right = 0; right <= 5; ++right) {
                offsets[left][right] = offset;
                int length = 1;
                for (int k = 0; k < left + right + 1; ++k) length *= 3;
                offset += length;
            }
        for (int point = 0; point < count; ++point) {
            const int x = point % size, y = point / size;
            // Floor boundaries, including the unequal regions on 20x20 boards.
            regions[point] = 1 + (((y+1)*3-1)/size)*3 + ((x+1)*3-1)/size;
            ++areas[0]; ++areas[regions[point]];
            for (int d = 0; d < 4; ++d) {
                auto& line = lines[point][d];
                int left = 0, right = 0;
                for (int k = -5; k <= 5; ++k) {
                    const int xx = x + k*directions[d].x, yy = y + k*directions[d].y;
                    if (xx < 0 || xx >= size || yy < 0 || yy >= size) continue;
                    line.cells.push_back(yy*size+xx);
                    left = std::max(left, -k); right = std::max(right, k);
                }
                line.offset = offsets[left][right];
            }
            std::vector<bool> dirty(count), halo(count);
            for (const auto& line : lines[point]) for (int q : line.cells) dirty[q] = true;
            for (int q = 0; q < count; ++q) if (dirty[q]) {
                affected[point].merged.push_back(q);
                for (int dy = -1; dy <= 1; ++dy) for (int dx = -1; dx <= 1; ++dx) {
                    const int xx = q%size+dx, yy = q/size+dy;
                    if (xx >= 0 && xx < size && yy >= 0 && yy < size) halo[yy*size+xx] = true;
                }
            }
            for (int q = 0; q < count; ++q) if (halo[q]) affected[point].spatial.push_back(q);
            // For an input at (x,y), cache output cells and the corresponding kernel tap.
            for (int dy = -1; dy <= 1; ++dy) for (int dx = -1; dx <= 1; ++dx) {
                const int xx = x+dx, yy = y+dy;
                if (xx >= 0 && xx < size && yy >= 0 && yy < size)
                    fanouts[point].push_back({yy*size+xx, (1-dy)*3+1-dx});
            }
        }
        for (int point = 0; point < count; ++point) for (int direction = 0; direction < 4; ++direction) {
            int power = 1;
            for (int cell : lines[point][direction].cells) {
                affected[cell].lines.push_back({point, direction, power});
                power *= 3;
            }
        }
    }
    void check(const Position& position) const {
        if (turn == Color::empty || position.hash() != hash || !model->supports(position))
            throw std::logic_error("NNUE accumulator does not match position");
    }
    void rebuild_patterns(const Position& position, int point) {
        auto& ids = pattern_ids[point];
        for (int d = 0; d < 4; ++d) {
            const auto& line = lines[point][d];
            ids[0][d] = ids[1][d] = line.offset;
            int power = 1;
            for (int cell : line.cells) {
                const auto color = position.at({cell%size, cell/size});
                if (color != Color::empty) {
                    const int digit = color == Color::black ? 1 : 2;
                    ids[0][d] += digit*power;
                    ids[1][d] += (3-digit)*power;
                }
                power *= 3;
            }
        }
    }
    void change_patterns(int point, Color color, int sign) {
        const int digit = static_cast<int>(color);
        for (const auto& line : affected[point].lines) {
            pattern_ids[line.point][0][line.direction] += sign*digit*line.power;
            pattern_ids[line.point][1][line.direction] += sign*(3-digit)*line.power;
        }
    }
    void update_merged(int point) {
        const auto& ids = pattern_ids[point];
        for (int side = 0; side < 2; ++side) {
            const auto* hv = model->tensors_[0].data();
            const auto* diag = model->tensors_[1].data();
            const int offset = (side*count+point)*c;
            changed[side*count+point] = nnue_kernels::merge(merged.data()+offset, deltas.data()+offset,
                hv+ids[side][0]*c, hv+ids[side][1]*c, diag+ids[side][2]*c, diag+ids[side][3]*c, c);
        }
    }
    void propagate(int point, bool incremental) {
        for (int side = 0; side < 2; ++side) {
            if (incremental && !changed[side*count+point]) continue;
            const auto* values = (incremental ? deltas.data() : merged.data()) + (side*count+point)*c;
            for (const auto& neighbor : fanouts[point])
                nnue_kernels::accumulate(preactivation.data()+(side*count+neighbor.point)*c,
                    values, spatial_weights.data()+neighbor.kernel*c, c);
        }
    }
    void update_spatial(int point) {
        for (int side = 0; side < 2; ++side)
            nnue_kernels::activate_pool(preactivation.data()+(side*count+point)*c,
                spatial.data()+(side*count+point)*c, pools.data()+side*10*c,
                pools.data()+(side*10+regions[point])*c, c);
    }
    template<typename T>
    void copy_points(std::vector<T>& saved, std::vector<T>& data,
                     const std::vector<int>& points, bool restore) {
        saved.resize(2*points.size()*c);
        std::size_t offset = 0;
        for (int side = 0; side < 2; ++side) for (int point : points) {
            auto* target = data.data()+(side*count+point)*c;
            if (restore) std::copy_n(saved.data()+offset, c, target);
            else std::copy_n(target, c, saved.data()+offset);
            offset += c;
        }
    }
    void input(bool value, std::span<std::int16_t> result) const {
        GOMOKU_SCOPE_PHASE(value ? profile::Phase::value_input : profile::Phase::policy_input);
        const int groups = value ? 10 : 1;
        for (int group = 0; group < groups; ++group) for (int relative = 0; relative < 2; ++relative) {
            const int side = (static_cast<int>(turn)-1+relative)%2;
            for (int channel = 0; channel < c; ++channel)
                result[(group*2+relative)*c+channel] = static_cast<std::int16_t>(
                    rounded(pools[(side*10+group)*c+channel], areas[group]));
        }
        result[groups*2*c] = turn == Color::black ? 256 : 0;
        result[groups*2*c+1] = static_cast<std::int16_t>(size*256/20);
    }
    void linear(int tensor, std::span<const std::int16_t> values, std::span<std::int64_t> result) const {
        GOMOKU_SCOPE_PHASE(tensor == 4 ? profile::Phase::value_hidden :
            tensor == 6 ? profile::Phase::value_output :
            tensor == 8 ? profile::Phase::policy_local :
            tensor == 10 ? profile::Phase::policy_global : profile::Phase::policy_output);
        const auto& weights = model->tensors_[tensor];
        const auto& biases = model->tensors_[tensor+1];
        for (std::size_t row = 0; row < biases.size(); ++row) {
            const auto* weight = weights.data()+row*values.size();
            result[row] = std::int64_t(biases[row])*256 + nnue_kernels::dot(weight, values.data(), static_cast<int>(values.size()));
        }
    }
};

NnueEvaluator::NnueEvaluator(std::shared_ptr<const NnueModel> model) {
    GOMOKU_SCOPE(evaluator);
    if (!model) throw std::invalid_argument("NNUE model required");
    state_ = std::make_unique<State>(std::move(model));
}
NnueEvaluator::~NnueEvaluator() = default;
void NnueEvaluator::reset(const Position& position) {
    GOMOKU_SCOPE(reset);
    auto& s = *state_;
    if (!s.model->supports(position)) throw std::invalid_argument("NNUE model does not support this board/rule");
    s.turn = position.turn(); s.hash = position.hash(); s.depth = 0;
    std::fill(s.spatial.begin(), s.spatial.end(), 0);
    std::fill(s.pools.begin(), s.pools.end(), 0);
    {
        GOMOKU_SCOPE(reset_features);
        for (int point = 0; point < s.count; ++point) {
            s.rebuild_patterns(position, point);
            s.update_merged(point);
        }
    }
    {
        GOMOKU_SCOPE(reset_spatial);
        for (int point = 0; point < 2*s.count; ++point) for (int channel = 0; channel < s.c; ++channel)
            s.preactivation[point*s.c+channel] = int(s.model->tensors_[3][channel])*256;
        for (int point = 0; point < s.count; ++point) s.propagate(point, false);
        for (int point = 0; point < s.count; ++point) s.update_spatial(point);
    }
}
void NnueEvaluator::push(const Position& position, Move move) {
    GOMOKU_SCOPE(push);
    auto& s = *state_;
    if (!s.model->supports(position) || s.turn == Color::empty || position.turn() == s.turn ||
        !position.contains(move) || position.at(move) != s.turn)
        throw std::logic_error("Invalid NNUE push");
    if (s.depth == static_cast<int>(s.frames.size())) s.frames.emplace_back();
    auto& frame = s.frames[s.depth];
    frame.point = move.y*s.size+move.x; frame.hash = s.hash;
    const auto& points = s.affected[frame.point];
    {
        GOMOKU_SCOPE(save);
        frame.pools = s.pools;
        s.copy_points(frame.merged, s.merged, points.merged, false);
        s.copy_points(frame.spatial, s.spatial, points.spatial, false);
        s.copy_points(frame.preactivation, s.preactivation, points.spatial, false);
    }
    {
        GOMOKU_SCOPE(features);
        s.change_patterns(frame.point, s.turn, 1);
        for (int point : points.merged) s.update_merged(point);
    }
    {
        GOMOKU_SCOPE(spatial);
        for (int point : points.merged) s.propagate(point, true);
        for (int point : points.spatial) s.update_spatial(point);
    }
    ++s.depth; s.hash = position.hash(); s.turn = position.turn();
}
void NnueEvaluator::pop() {
    GOMOKU_SCOPE(pop);
    auto& s = *state_;
    if (s.depth == 0) throw std::logic_error("NNUE stack underflow");
    auto& frame = s.frames[--s.depth];
    const auto& points = s.affected[frame.point];
    s.copy_points(frame.merged, s.merged, points.merged, true);
    s.copy_points(frame.spatial, s.spatial, points.spatial, true);
    s.copy_points(frame.preactivation, s.preactivation, points.spatial, true);
    s.change_patterns(frame.point, opposite(s.turn), -1);
    s.pools = frame.pools; s.hash = frame.hash; s.turn = opposite(s.turn);
}
std::array<double, 3> NnueEvaluator::value_logits(const Position& position) const {
    GOMOKU_SCOPE(value);
    auto& s = *state_;
    s.check(position);
    s.input(true, s.value_input);
    s.linear(4, s.value_input, s.value_hidden);
    {
        GOMOKU_SCOPE(value_activation);
        std::transform(s.value_hidden.begin(), s.value_hidden.end(), s.value_activated.begin(), activate);
    }
    s.linear(6, s.value_activated, s.value_output);
    return {s.value_output[0]/262144.0, s.value_output[1]/262144.0, s.value_output[2]/262144.0};
}
int NnueEvaluator::evaluate(const Position& position) const {
    const auto logits = value_logits(position);
    GOMOKU_SCOPE(score);
    const auto maximum = *std::max_element(logits.begin(), logits.end());
    const double win = std::exp(logits[0]-maximum), draw = std::exp(logits[1]-maximum), loss = std::exp(logits[2]-maximum);
    // A bounded side-to-move score. These are evaluation units, not mate scores
    // or an Elo estimate; tanh(score / 600) recovers P(win)-P(loss).
    return static_cast<int>(std::lround(600*std::atanh(std::clamp((win-loss)/(win+draw+loss), -0.999999, 0.999999))));
}
std::vector<double> NnueEvaluator::move_scores(const Position& position, std::span<const Move> moves) const {
    GOMOKU_SCOPE(policy);
    auto& s = *state_;
    s.check(position);
    s.input(false, s.policy_input);
    s.linear(10, s.policy_input, s.policy_global);
    std::vector<double> result;
    result.reserve(moves.size());
    for (Move move : moves) {
        if (!position.contains(move)) throw std::invalid_argument("NNUE policy move outside board");
        if (position.at(move) != Color::empty) { result.push_back(-1e9); continue; }
        const int point = move.y*s.size+move.x;
        {
            GOMOKU_SCOPE(policy_pair);
            for (int relative = 0; relative < 2; ++relative) {
                const int side = (static_cast<int>(s.turn)-1+relative)%2;
                std::copy_n(s.spatial.data()+(side*s.count+point)*s.c, s.c, s.paired.data()+relative*s.c);
            }
        }
        s.linear(8, s.paired, s.policy_local);
        {
            GOMOKU_SCOPE(policy_activation);
            for (std::size_t i = 0; i < s.policy_local.size(); ++i)
                s.policy_activated[i] = activate(s.policy_local[i]+s.policy_global[i]);
        }
        s.linear(12, s.policy_activated, s.policy_output);
        result.push_back(s.policy_output[0]/262144.0);
    }
    return result;
}
} // namespace gomoku
