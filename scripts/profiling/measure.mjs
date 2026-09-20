// Shared by Node and a browser Worker. All timing samples are sequential.
export const signature = ({ result }) => {
  const { elapsedMs: _, ...stable } = result;
  return JSON.stringify(stable);
};
export const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

export async function measure(engines, cases, options = {}, progress = () => {}) {
  const { repeats = 5, warmups = 2 } = options;
  const rows = [];
  for (const test of cases) {
    const payload = { position: test.position, maxDepth: test.maxDepth };
    let expected;
    const samples = { baseline: [], profiled: [] };
    for (let pass = -warmups; pass < repeats; pass++) {
      // Alternate build order to reduce bias from CPU frequency / thermal drift.
      const order = Math.abs(pass) % 2 ? ["profiled", "baseline"] : ["baseline", "profiled"];
      for (const kind of order) {
        const result = await engines[kind].request(payload);
        if (result.error) throw new Error(result.error);
        if (result.result.reason !== "completed")
          throw new Error("Unfinished fixed-depth benchmark");
        if (result.profiled !== (kind === "profiled"))
          throw new Error("Wrong instrumentation build");
        const stable = signature(result);
        if (expected && expected !== stable)
          throw new Error(`Search changed: ${test.id} / ${kind}`);
        expected = stable;
        if (kind === "profiled") {
          const sum = result.phases.reduce((total, phase) => total + phase.exclusiveMs, 0);
          const total = result.phases.find((phase) => phase.name === "total")?.inclusiveMs;
          if (total === undefined || Math.abs(sum - total) > 0.001)
            throw new Error("Exclusive timers do not reconcile to total");
          if (result.phases.some((phase) => phase.exclusiveMs < -0.000001))
            throw new Error("Negative exclusive timing");
        }
        if (pass >= 0) samples[kind].push(result);
      }
      progress(
        `${test.label}: ${pass < 0 ? "warmup" : "sample"} ${pass < 0 ? pass + warmups + 1 : pass + 1}`,
      );
    }
    const summarize = (samples) => {
      const phases = samples[0].phases.map(({ name }) => {
        const matches = samples.map((sample) => sample.phases.find((phase) => phase.name === name));
        return {
          name,
          calls: matches[0].calls,
          meanInclusiveMs:
            matches.reduce((sum, item) => sum + item.inclusiveMs, 0) / matches.length,
          meanExclusiveMs:
            matches.reduce((sum, item) => sum + item.exclusiveMs, 0) / matches.length,
        };
      });
      return {
        medianMs: median(samples.map((sample) => sample.wallMs)),
        minMs: Math.min(...samples.map((sample) => sample.wallMs)),
        maxMs: Math.max(...samples.map((sample) => sample.wallMs)),
        meanMs: samples.reduce((sum, sample) => sum + sample.wallMs, 0) / samples.length,
        result: samples[0].result,
        phases,
      };
    };
    const baseline = summarize(samples.baseline);
    const profiled = summarize(samples.profiled);
    const total = profiled.phases.find((phase) => phase.name === "total").meanInclusiveMs;
    for (const phase of profiled.phases) {
      phase.exclusivePercent = (100 * phase.meanExclusiveMs) / total;
      phase.meanInclusiveUsPerCall = (phase.meanInclusiveMs * 1000) / phase.calls;
    }
    rows.push({
      ...test,
      baseline,
      profiled,
      overheadPercent: 100 * (profiled.medianMs / baseline.medianMs - 1),
      samples,
    });
    progress(
      `${test.label}: baseline ${baseline.medianMs.toFixed(1)} ms, profiled ${profiled.medianMs.toFixed(1)} ms`,
    );
  }
  const iterations = await engines.baseline.request({
    position: cases[0].position,
    maxDepth: cases[0].maxDepth,
    observe: true,
  });
  const timeLimited = [];
  for (let repeat = 0; repeat < repeats; repeat++) {
    timeLimited.push(
      await engines.baseline.request({
        position: cases[0].position,
        maxDepth: 8,
        timeMs: 300,
        observe: true,
      }),
    );
  }
  return { repeats, warmups, rows, iterations, timeLimited };
}

export async function wasmEngine(createModule, bytes, moduleOptions = {}) {
  const start = performance.now();
  const module = await createModule(moduleOptions);
  const moduleReadyMs = performance.now() - start;
  const copyStart = performance.now();
  const pointer = module._malloc(bytes.length);
  if (!pointer) throw new Error("WASM allocation failed");
  let copyMs, loadMs;
  try {
    module.HEAPU8.set(bytes, pointer);
    copyMs = performance.now() - copyStart;
    const loadStart = performance.now();
    const error = module.ccall(
      "gomoku_bench_load_model",
      "string",
      ["number", "number"],
      [pointer, bytes.length],
    );
    loadMs = performance.now() - loadStart;
    if (error) throw new Error(error);
  } finally {
    module._free(pointer);
  }
  return {
    startup: { moduleReadyMs, copyMs, loadMs },
    request(payload) {
      const start = performance.now();
      const result = JSON.parse(
        module.ccall("gomoku_bench", "string", ["string"], [JSON.stringify(payload)]),
      );
      result.bridgeMs = performance.now() - start;
      return result;
    },
  };
}
