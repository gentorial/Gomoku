#pragma once
#include <cstdint>

namespace gomoku::nnue_kernels {
// Model validation bounds weights to +/-8192 and activations to 0..256.
// Dot products widen partial sums before an int32 lane could overflow.
std::int64_t dot(const std::int16_t* weights, const std::int16_t* input, int count);
// Four codebook vectors in [-256,256], clipped to [0,256]. Returns whether changed.
bool merge(std::int16_t* output, std::int16_t* delta, const std::int16_t* a,
           const std::int16_t* b, const std::int16_t* c, const std::int16_t* d, int count);
// Exact depthwise convolution update. Preactivations stay within +/-20,971,520.
void accumulate(std::int32_t* output, const std::int16_t* delta,
                const std::int16_t* weights, int count);
// Round to nearest even at scale 1024, clip, and update two pool sums exactly.
void activate_pool(const std::int32_t* input, std::int16_t* output,
                   std::int32_t* global, std::int32_t* region, int count);
} // namespace gomoku::nnue_kernels
