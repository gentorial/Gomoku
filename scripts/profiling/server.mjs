import { createServer } from "node:http";
import { createReadStream } from "node:fs";
import { readFile, writeFile, mkdir, stat } from "node:fs/promises";
import { resolve, extname } from "node:path";
import { execFileSync } from "node:child_process";
import os from "node:os";
import { root } from "../process.mjs";

const port = Number(process.env.GOMOKU_PROFILE_PORT || 4177);
const pin = JSON.parse(await readFile(resolve(root, "models/web-model.json"), "utf8"));
const paths = new Map([
  ["/", "scripts/profiling/browser.html"],
  ["/worker.mjs", "scripts/profiling/browser-worker.mjs"],
  ["/measure.mjs", "scripts/profiling/measure.mjs"],
  ["/cases.json", "tests/fixtures/engine-profile.json"],
  ["/report.html", "artifacts/profiling/report.html"],
  ["/weights.gnn", `apps/web/public/models/${pin.sha256}/weights.gnn`],
]);
for (const kind of ["baseline", "profiled"])
  for (const extension of ["mjs", "wasm"])
    paths.set(
      `/${kind}/gomoku-bench.${extension}`,
      `build/profiling/wasm/${kind}/bin/gomoku-bench.${extension}`,
    );
const types = {
  ".html": "text/html; charset=utf-8",
  ".mjs": "text/javascript",
  ".wasm": "application/wasm",
  ".json": "application/json",
};
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
  build: "Emscripten 4.0.23 Release; exact SIMD128 kernels; single search thread; no LTO",
  method:
    "Fixed depth, 600 s guard; sequential alternating baseline/profiled builds; startup excluded",
};
const server = createServer(async (request, response) => {
  try {
    if (request.url === "/metadata.json" && request.method === "GET") {
      response.setHeader("Content-Type", "application/json");
      return response.end(JSON.stringify(metadata));
    }
    if (request.url === "/results" && request.method === "POST") {
      if (request.headers.origin !== `http://127.0.0.1:${port}`) {
        response.writeHead(403);
        return response.end();
      }
      const chunks = [];
      let length = 0;
      for await (const chunk of request) {
        length += chunk.length;
        if (length > 5 * 1024 * 1024) throw new Error("Report too large");
        chunks.push(chunk);
      }
      const report = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (report.metadata?.model?.sha256 !== pin.sha256 || report.rows?.length !== 7)
        throw new Error("Unexpected report");
      const directory = resolve(root, "artifacts/profiling");
      await mkdir(directory, { recursive: true });
      await writeFile(
        resolve(directory, "browser-wasm.json"),
        JSON.stringify(report, null, 2) + "\n",
      );
      console.log("Browser report saved: artifacts/profiling/browser-wasm.json");
      response.writeHead(200);
      return response.end("ok");
    }
    const file = paths.get(request.url);
    if (request.method !== "GET" || !file) {
      response.writeHead(404);
      return response.end();
    }
    const path = resolve(root, file);
    const info = await stat(path);
    response.setHeader("Content-Type", types[extname(path)] || "application/octet-stream");
    response.setHeader("Content-Length", info.size);
    response.setHeader("Cache-Control", "no-store");
    createReadStream(path).pipe(response);
  } catch (error) {
    response.writeHead(500);
    response.end(error.message);
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(`Gomoku browser profiler: http://127.0.0.1:${port}/`),
);
