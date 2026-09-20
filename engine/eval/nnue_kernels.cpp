#include "nnue_kernels.h"
#include <algorithm>
#if defined(GOMOKU_SIMD) && defined(__wasm_simd128__)
#include <wasm_simd128.h>
#define GOMOKU_KERNEL_WASM
#elif defined(GOMOKU_SIMD) && (defined(__SSE2__) || defined(_M_X64))
#include <emmintrin.h>
#define GOMOKU_KERNEL_SSE2
#endif

namespace gomoku::nnue_kernels {
std::int64_t dot(const std::int16_t* weights, const std::int16_t* input, int count) {
    std::int64_t result = 0;
    int i = 0;
#if defined(GOMOKU_KERNEL_WASM) || defined(GOMOKU_KERNEL_SSE2)
    while (i + 8 <= count) {
        // 256 products per block: total absolute sum <= 536,870,912.
        const int end = i + std::min(256, (count - i) / 8 * 8);
#ifdef GOMOKU_KERNEL_WASM
        auto sum = wasm_i32x4_splat(0);
        for (; i < end; i += 8)
            sum = wasm_i32x4_add(sum, wasm_i32x4_dot_i16x8(wasm_v128_load(weights+i), wasm_v128_load(input+i)));
        std::int32_t lanes[4];
        wasm_v128_store(lanes, sum);
#else
        auto sum = _mm_setzero_si128();
        for (; i < end; i += 8)
            sum = _mm_add_epi32(sum, _mm_madd_epi16(
                _mm_loadu_si128(reinterpret_cast<const __m128i*>(weights+i)),
                _mm_loadu_si128(reinterpret_cast<const __m128i*>(input+i))));
        std::int32_t lanes[4];
        _mm_storeu_si128(reinterpret_cast<__m128i*>(lanes), sum);
#endif
        for (auto lane : lanes) result += lane;
    }
#endif
    for (; i < count; ++i) result += std::int64_t(weights[i]) * input[i];
    return result;
}

bool merge(std::int16_t* output, std::int16_t* delta, const std::int16_t* a,
           const std::int16_t* b, const std::int16_t* c, const std::int16_t* d, int count) {
    bool changed = false;
    int i = 0;
#ifdef GOMOKU_KERNEL_WASM
    for (; i + 8 <= count; i += 8) {
        auto value = wasm_i16x8_add(wasm_i16x8_add(wasm_v128_load(a+i), wasm_v128_load(b+i)),
                                  wasm_i16x8_add(wasm_v128_load(c+i), wasm_v128_load(d+i)));
        value = wasm_i16x8_min(wasm_i16x8_max(value, wasm_i16x8_splat(0)), wasm_i16x8_splat(256));
        const auto difference = wasm_i16x8_sub(value, wasm_v128_load(output+i));
        changed |= wasm_v128_any_true(difference);
        wasm_v128_store(output+i, value); wasm_v128_store(delta+i, difference);
    }
#elif defined(GOMOKU_KERNEL_SSE2)
    for (; i + 8 <= count; i += 8) {
        const auto load = [](const std::int16_t* p) { return _mm_loadu_si128(reinterpret_cast<const __m128i*>(p)); };
        auto value = _mm_add_epi16(_mm_add_epi16(load(a+i), load(b+i)), _mm_add_epi16(load(c+i), load(d+i)));
        value = _mm_min_epi16(_mm_max_epi16(value, _mm_setzero_si128()), _mm_set1_epi16(256));
        const auto difference = _mm_sub_epi16(value, load(output+i));
        changed |= _mm_movemask_epi8(_mm_cmpeq_epi16(difference, _mm_setzero_si128())) != 0xffff;
        _mm_storeu_si128(reinterpret_cast<__m128i*>(output+i), value);
        _mm_storeu_si128(reinterpret_cast<__m128i*>(delta+i), difference);
    }
#endif
    for (; i < count; ++i) {
        const auto value = static_cast<std::int16_t>(std::clamp(int(a[i])+b[i]+c[i]+d[i], 0, 256));
        delta[i] = static_cast<std::int16_t>(value-output[i]);
        changed |= delta[i] != 0; output[i] = value;
    }
    return changed;
}

void accumulate(std::int32_t* output, const std::int16_t* delta, const std::int16_t* weights, int count) {
    int i = 0;
#ifdef GOMOKU_KERNEL_WASM
    for (; i + 8 <= count; i += 8) {
        const auto d = wasm_v128_load(delta+i), w = wasm_v128_load(weights+i);
        wasm_v128_store(output+i, wasm_i32x4_add(wasm_v128_load(output+i), wasm_i32x4_extmul_low_i16x8(d,w)));
        wasm_v128_store(output+i+4, wasm_i32x4_add(wasm_v128_load(output+i+4), wasm_i32x4_extmul_high_i16x8(d,w)));
    }
#elif defined(GOMOKU_KERNEL_SSE2)
    for (; i + 8 <= count; i += 8) {
        const auto d = _mm_loadu_si128(reinterpret_cast<const __m128i*>(delta+i));
        const auto w = _mm_loadu_si128(reinterpret_cast<const __m128i*>(weights+i));
        const auto low = _mm_mullo_epi16(d,w), high = _mm_mulhi_epi16(d,w);
        auto* target = reinterpret_cast<__m128i*>(output+i);
        _mm_storeu_si128(target, _mm_add_epi32(_mm_loadu_si128(target), _mm_unpacklo_epi16(low,high)));
        _mm_storeu_si128(target+1, _mm_add_epi32(_mm_loadu_si128(target+1), _mm_unpackhi_epi16(low,high)));
    }
#endif
    for (; i < count; ++i) output[i] += int(delta[i]) * weights[i];
}

void activate_pool(const std::int32_t* input, std::int16_t* output,
                   std::int32_t* global, std::int32_t* region, int count) {
    int i = 0;
#ifdef GOMOKU_KERNEL_WASM
    const auto rounded = [](v128_t x) {
        const auto odd = wasm_v128_and(wasm_i32x4_shr(x,10), wasm_i32x4_splat(1));
        return wasm_i32x4_shr(wasm_i32x4_add(x, wasm_i32x4_add(wasm_i32x4_splat(511), odd)),10);
    };
    for (; i + 8 <= count; i += 8) {
        auto next = wasm_i16x8_narrow_i32x4(rounded(wasm_v128_load(input+i)), rounded(wasm_v128_load(input+i+4)));
        next = wasm_i16x8_min(wasm_i16x8_max(next, wasm_i16x8_splat(0)), wasm_i16x8_splat(256));
        const auto delta = wasm_i16x8_sub(next, wasm_v128_load(output+i));
        const auto low = wasm_i32x4_extend_low_i16x8(delta), high = wasm_i32x4_extend_high_i16x8(delta);
        for (auto* pool : {global+i, region+i}) {
            wasm_v128_store(pool, wasm_i32x4_add(wasm_v128_load(pool), low));
            wasm_v128_store(pool+4, wasm_i32x4_add(wasm_v128_load(pool+4), high));
        }
        wasm_v128_store(output+i, next);
    }
#elif defined(GOMOKU_KERNEL_SSE2)
    const auto rounded = [](__m128i x) {
        const auto odd = _mm_and_si128(_mm_srai_epi32(x,10), _mm_set1_epi32(1));
        return _mm_srai_epi32(_mm_add_epi32(x, _mm_add_epi32(_mm_set1_epi32(511), odd)),10);
    };
    for (; i + 8 <= count; i += 8) {
        auto next = _mm_packs_epi32(rounded(_mm_loadu_si128(reinterpret_cast<const __m128i*>(input+i))),
                                  rounded(_mm_loadu_si128(reinterpret_cast<const __m128i*>(input+i+4))));
        next = _mm_min_epi16(_mm_max_epi16(next, _mm_setzero_si128()), _mm_set1_epi16(256));
        const auto delta = _mm_sub_epi16(next, _mm_loadu_si128(reinterpret_cast<const __m128i*>(output+i)));
        const auto sign = _mm_srai_epi16(delta,15);
        const auto low = _mm_unpacklo_epi16(delta,sign), high = _mm_unpackhi_epi16(delta,sign);
        for (auto* pool : {global+i, region+i}) {
            auto* target = reinterpret_cast<__m128i*>(pool);
            _mm_storeu_si128(target, _mm_add_epi32(_mm_loadu_si128(target), low));
            _mm_storeu_si128(target+1, _mm_add_epi32(_mm_loadu_si128(target+1), high));
        }
        _mm_storeu_si128(reinterpret_cast<__m128i*>(output+i), next);
    }
#endif
    for (; i < count; ++i) {
        // Signed right shift is floor division in C++20; this also handles negative ties.
        const int value = (input[i] + 511 + ((input[i] >> 10) & 1)) >> 10;
        const auto next = static_cast<std::int16_t>(std::clamp(value, 0, 256));
        const int delta = next-output[i];
        global[i] += delta; region[i] += delta; output[i] = next;
    }
}
} // namespace gomoku::nnue_kernels
