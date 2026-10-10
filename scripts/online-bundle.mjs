// Bundles the online play server into one ESM file plus the WASM rules engine and
// config/online.json (run it with GOMOKU_WASM and GOMOKU_CONFIG naming those copies),
// so a host needs only Node.js (no pnpm install, compilers or native engine).
import { copyFile, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { build } from "esbuild";
import { root } from "./process.mjs";

const out = resolve(root, "dist/online");
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await build({
  entryPoints: [resolve(root, "apps/server/src/main.ts")],
  outfile: resolve(out, "server.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  // Bundled CommonJS dependencies still call require() for Node built-ins.
  banner: {
    js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);",
  },
  logLevel: "warning",
});
await copyFile(
  resolve(root, "packages/engine-wasm/generated/gomoku-engine.wasm"),
  resolve(out, "gomoku-engine.wasm"),
);
await copyFile(resolve(root, "config/online.json"), resolve(out, "online.json"));
console.log("Online server bundle: " + out);
