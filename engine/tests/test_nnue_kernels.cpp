#include "../eval/nnue_kernels.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

using namespace gomoku::nnue_kernels;
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
int main() {
    try {
        std::mt19937 random(101);
        for (int size : {1,4,7,8,9,15,16,63,64,65,255,256,257,1296,20496}) {
            std::vector<std::int16_t> weights(size), input(size);
            for (int mode = 0; mode < 3; ++mode) {
                std::int64_t expected = 0;
                for (int i = 0; i < size; ++i) {
                    weights[i] = static_cast<std::int16_t>(mode == 0 ? 8192 : mode == 1 ? -8192 : int(random()%16385)-8192);
                    input[i] = static_cast<std::int16_t>(mode < 2 ? 256 : random()%257);
                    expected += std::int64_t(weights[i])*input[i];
                }
                require(dot(weights.data(), input.data(), size) == expected, "SIMD dot or tail overflowed");
            }
            std::vector<std::int16_t> a(size), b(size), c(size), d(size), value(size), delta(size);
            std::vector<std::int32_t> sum(size), expected_sum(size);
            for (int i = 0; i < size; ++i) {
                a[i] = static_cast<std::int16_t>(int(random()%513)-256);
                b[i] = static_cast<std::int16_t>(int(random()%513)-256);
                c[i] = static_cast<std::int16_t>(int(random()%513)-256);
                d[i] = static_cast<std::int16_t>(int(random()%513)-256);
                value[i] = static_cast<std::int16_t>(random()%257);
            }
            const auto old = value;
            merge(value.data(), delta.data(), a.data(), b.data(), c.data(), d.data(), size);
            for (int i = 0; i < size; ++i) {
                require(value[i] == std::clamp(int(a[i])+b[i]+c[i]+d[i], 0, 256), "SIMD merge clipping differs");
                require(delta[i] == value[i]-old[i], "Merged feature delta differs");
                sum[i] = int(random()%2000001)-1000000;
                expected_sum[i] = sum[i]+int(delta[i])*weights[i];
            }
            accumulate(sum.data(), delta.data(), weights.data(), size);
            require(sum == expected_sum, "Signed convolution delta differs");
            require(!merge(value.data(), delta.data(), a.data(), b.data(), c.data(), d.data(), size),
                    "Unchanged features must skip convolution propagation");
            require(std::all_of(delta.begin(), delta.end(), [](auto x) { return x == 0; }), "Unchanged deltas must be zero");
            const std::array<int,18> boundary{-20971520,-513,-512,-511,-1,0,511,512,513,1023,1024,1535,1536,1537,262143,262144,262145,20971520};
            std::vector<std::int32_t> global(size, 50000), region(size, 3000);
            const auto previous = value;
            for (int i = 0; i < size; ++i) sum[i] = boundary[i%boundary.size()];
            activate_pool(sum.data(), value.data(), global.data(), region.data(), size);
            for (int i = 0; i < size; ++i) {
                const int expected = std::clamp(static_cast<int>(std::nearbyint(double(sum[i])/1024)), 0, 256);
                require(value[i] == expected, "SIMD activation must round ties to even");
                require(global[i] == 50000+expected-previous[i] && region[i] == 3000+expected-previous[i],
                        "Pool updates must use the activated feature delta");
            }
        }
        std::cout << "NNUE kernel bounds, rounding, signed deltas and vector tails passed\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
