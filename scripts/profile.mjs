import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import { spawn, execFileSync } from "node:child_process";
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import os from "node:os";
import { root, run } from "./process.mjs";
import { measure, wasmEngine } from "./profiling/measure.mjs";

const output = resolve(root, "artifacts/profiling");
const mode = process.argv[2] || "all";
const runtime = process.argv[3] || "all";
const repeats = Number(process.env.GOMOKU_BENCH_REPEATS || 5);
const warmups = Number(process.env.GOMOKU_BENCH_WARMUPS || 2);
const buildDirectory = (runtime, kind) => resolve(root, "build/profiling", runtime, kind);
const pin = JSON.parse(await readFile(resolve(root, "models/web-model.json"), "utf8"));
const modelPath = resolve(root, "apps/web/public/models", pin.sha256, "weights.gnn");
const metadata = {
  measuredAt: new Date().toISOString(),
  commit: execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
    windowsHide: true,
  }).trim(),
  workingTree:
    "Incremental NNUE, exact SIMD kernels, TT/PVS and staged move selection; pinned model unchanged",
  model: pin,
  cpu: os.cpus()[0]?.model,
  logicalCpus: os.cpus().length,
  platform: `${os.platform()} ${os.release()} ${os.arch()}`,
  node: process.version,
  build: "Release, exact SSE2/native or SIMD128/WASM kernels, single search thread; no LTO",
  method:
    "Fixed depth, 600 s guard; sequential alternating baseline/profiled builds; startup excluded",
};

async function build(runtime) {
  for (const kind of ["baseline", "profiled"]) {
    const directory = buildDirectory(runtime, kind);
    const args = [
      "-S",
      root,
      "-B",
      directory,
      "-G",
      "Ninja",
      "-DCMAKE_BUILD_TYPE=Release",
      "-DGOMOKU_BUILD_BENCHMARKS=ON",
      `-DGOMOKU_PROFILE=${kind === "profiled" ? "ON" : "OFF"}`,
      "-DGOMOKU_BUILD_WORKER=OFF",
      "-DGOMOKU_BUILD_WASM=OFF",
      "-DGOMOKU_BUILD_GOMOCUP=OFF",
      "-DGOMOKU_BUILD_TESTS=ON",
    ];
    // Reuse an already checked-out dependency if available; normal FetchContent is the fallback.
    const dependency = resolve(root, "build/dev/_deps/nlohmann_json-src");
    try {
      await access(resolve(dependency, "CMakeLists.txt"));
      args.push(`-DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=${dependency}`);
    } catch {
      /* A clean machine may fetch the pinned dependency normally. */
    }
    if (runtime === "wasm") {
      const sdk = process.env.EMSDK || resolve(root, ".tools/emsdk");
      await run(process.env.PYTHON || "python", [
        resolve(sdk, "upstream/emscripten/emcmake.py"),
        "cmake",
        ...args,
      ]);
    } else {
      await run("cmake", args);
    }
    await run("cmake", ["--build", directory, "--parallel", "4"]);
    await run("ctest", ["--test-dir", directory, "--output-on-failure"]);
  }
}

async function nativeEngine(kind) {
  const executable = resolve(
    buildDirectory("native", kind),
    "bin/gomoku-bench" + (process.platform === "win32" ? ".exe" : ""),
  );
  const processHandle = spawn(executable, [modelPath], {
    cwd: root,
    stdio: ["pipe", "pipe", "inherit"],
    windowsHide: true,
  });
  const lines = createInterface({ input: processHandle.stdout })[Symbol.asyncIterator]();
  const read = async () => {
    const line = await lines.next();
    if (line.done) throw new Error("Native benchmark exited unexpectedly");
    return JSON.parse(line.value);
  };
  const startup = await read();
  if (!startup.ready) throw new Error("Native benchmark did not initialize");
  return {
    startup,
    async request(payload) {
      const start = performance.now();
      processHandle.stdin.write(JSON.stringify(payload) + "\n");
      const result = await read();
      result.bridgeMs = performance.now() - start;
      return result;
    },
    close() {
      processHandle.stdin.end();
    },
  };
}

async function benchmark(runtime) {
  const bytes = await readFile(modelPath);
  if (bytes.length !== pin.bytes || createHash("sha256").update(bytes).digest("hex") !== pin.sha256)
    throw new Error("Pinned model integrity mismatch");
  const cases = JSON.parse(
    await readFile(resolve(root, "tests/fixtures/engine-profile.json"), "utf8"),
  );
  const engines = {};
  try {
    for (const kind of ["baseline", "profiled"]) {
      if (runtime === "native") engines[kind] = await nativeEngine(kind);
      else {
        const directory = resolve(buildDirectory("wasm", kind), "bin");
        const { default: createModule } = await import(
          pathToFileURL(resolve(directory, "gomoku-bench.mjs"))
        );
        engines[kind] = await wasmEngine(createModule, bytes, {
          wasmBinary: await readFile(resolve(directory, "gomoku-bench.wasm")),
        });
      }
    }
    const result = await measure(engines, cases, { repeats, warmups }, console.log);
    const report = {
      metadata: { ...metadata, runtime },
      startup: { baseline: engines.baseline.startup, profiled: engines.profiled.startup },
      ...result,
    };
    await mkdir(output, { recursive: true });
    const path = resolve(output, `${runtime}.json`);
    await writeFile(path, JSON.stringify(report, null, 2) + "\n");
    console.log(`Saved ${path}`);
  } finally {
    for (const engine of Object.values(engines)) engine.close?.();
  }
}

if (!["all", "build", "run"].includes(mode) || !["all", "native", "wasm"].includes(runtime))
  throw new Error("Usage: node scripts/profile.mjs [all|build|run] [all|native|wasm]");
if (!Number.isInteger(repeats) || repeats < 1 || !Number.isInteger(warmups) || warmups < 0)
  throw new Error("Invalid benchmark repeats/warmups");
for (const target of runtime === "all" ? ["native", "wasm"] : [runtime]) {
  if (mode !== "run") await build(target);
  if (mode !== "build") await benchmark(target);
}
