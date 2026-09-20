import { access } from "node:fs/promises";
import { resolve } from "node:path";
import { command, root, run, stop } from "./process.mjs";
import { stageModel } from "./model.mjs";

const withServer = process.argv.includes("--server");
await stageModel();
await access(resolve(root, "packages/engine-wasm/generated/gomoku-engine.wasm")).catch(() => {
  throw new Error("Build the browser engine first: pnpm wasm:setup && pnpm wasm:build");
});
if (withServer)
  await access(
    process.env.GOMOKU_ENGINE_PATH ||
      resolve(root, "build/dev/bin/gomoku-worker" + (process.platform === "win32" ? ".exe" : "")),
  ).catch(() => {
    throw new Error("Build the native engine first: pnpm engine:build");
  });
await run("pnpm", ["build:packages"]);
const children = [
  ...(withServer
    ? [
        command("pnpm", ["--filter", "@gomoku/server", "dev"], {
          detached: process.platform !== "win32",
        }),
      ]
    : []),
  command("pnpm", ["--filter", "@gomoku/web", "dev"], { detached: process.platform !== "win32" }),
];
let stopping = false;
function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) stop(child);
  process.exitCode = code;
}
for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => shutdown());
for (const child of children) {
  child.on("error", (error) => {
    console.error(error.message);
    shutdown(1);
  });
  child.on("exit", (code) => {
    if (!stopping) shutdown(code || 0);
  });
}
