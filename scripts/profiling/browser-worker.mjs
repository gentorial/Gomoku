import createBaseline from "/baseline/gomoku-bench.mjs";
import createProfiled from "/profiled/gomoku-bench.mjs";
import { measure, wasmEngine } from "/measure.mjs";

self.onmessage = async () => {
  try {
    const metadata = await (await fetch("/metadata.json")).json();
    const cases = await (await fetch("/cases.json")).json();
    const bytes = new Uint8Array(await (await fetch("/weights.gnn")).arrayBuffer());
    const digest = Array.from(
      new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      (value) => value.toString(16).padStart(2, "0"),
    ).join("");
    if (digest !== metadata.model.sha256 || bytes.length !== metadata.model.bytes)
      throw new Error("Pinned model integrity mismatch");
    self.postMessage({ progress: "模型校验通过；准备两个独立 WASM 实例" });
    const engines = {
      baseline: await wasmEngine(createBaseline, bytes),
      profiled: await wasmEngine(createProfiled, bytes),
    };
    const result = await measure(engines, cases, { repeats: 5, warmups: 2 }, (progress) =>
      self.postMessage({ progress }),
    );
    const report = {
      metadata: {
        ...metadata,
        runtime: "browser-wasm",
        userAgent: navigator.userAgent,
        crossOriginIsolated: self.crossOriginIsolated,
        hardwareConcurrency: navigator.hardwareConcurrency,
      },
      startup: { baseline: engines.baseline.startup, profiled: engines.profiled.startup },
      ...result,
    };
    const response = await fetch("/results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(report),
    });
    if (!response.ok) throw new Error("Could not save browser report");
    self.postMessage({ complete: true, report });
  } catch (error) {
    self.postMessage({ error: error.stack || String(error) });
  }
};
