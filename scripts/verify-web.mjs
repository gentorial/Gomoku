import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { root } from "./process.mjs";

// Check the actual deployable directory, including URLs embedded in worker JS.
// A successful Vite build alone would not catch a wrong Pages base or missing WASM.
const base = process.env.GOMOKU_WEB_BASE || "/";
assert.ok(base.startsWith("/") && base.endsWith("/"), "GOMOKU_WEB_BASE must start and end with /");
const dist = resolve(root, "apps/web/dist");
const verified = new Set();
async function verifyUrl(url) {
  assert.ok(url.startsWith(base), "Asset does not use deployment base " + base + ": " + url);
  const file = resolve(dist, decodeURIComponent(url.slice(base.length)));
  const local = relative(dist, file);
  assert.ok(
    local && !isAbsolute(local) && !local.startsWith(".." + sep) && local !== "..",
    "Asset escapes dist: " + url,
  );
  assert.ok((await stat(file)).isFile(), "Asset is missing: " + url);
  verified.add(local.replaceAll(sep, "/"));
}
const html = await readFile(resolve(dist, "index.html"), "utf8");
const htmlUrls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((match) => match[1]);
assert.ok(
  htmlUrls.some((url) => url.endsWith(".js")),
  "Missing entry script",
);
assert.ok(
  htmlUrls.some((url) => url.endsWith(".css")),
  "Missing stylesheet",
);
for (const url of htmlUrls) await verifyUrl(url);

const assets = await readdir(resolve(dist, "assets"));
for (const name of assets.filter((name) => name.endsWith(".js"))) {
  const source = await readFile(resolve(dist, "assets", name), "utf8");
  // Vite emits quoted absolute URLs for Worker and WASM assets. Support all JS
  // quote styles; relative Emscripten fallback names are overridden by locateFile.
  for (const match of source.matchAll(/["'`]([^"'`\s]+\.(?:js|wasm))["'`]/g)) {
    if (match[1].startsWith("/")) await verifyUrl(match[1]);
  }
}
assert.ok(
  [...verified].some((name) => /engine\.worker-.*\.js$/.test(name)),
  "Missing bundled Worker URL",
);
const wasm = [...verified].find((name) => name.endsWith(".wasm"));
assert.ok(wasm, "Missing bundled WASM URL");
const bytes = await readFile(resolve(dist, wasm));
assert.ok(WebAssembly.validate(bytes), "Invalid WebAssembly binary");
const modelManifest = resolve(dist, "models/active.json");
const model = await readFile(modelManifest, "utf8").catch((error) => {
  if (error.code === "ENOENT") return null;
  throw error;
});
if (model) {
  const manifest = JSON.parse(model);
  assert.match(manifest.weights, /^[a-f0-9]{64}\/weights\.gnn$/);
  await verifyUrl(base + "models/" + manifest.weights);
  const weights = await readFile(resolve(dist, "models", manifest.weights));
  assert.equal(weights.length, manifest.bytes);
  assert.equal(createHash("sha256").update(weights).digest("hex"), manifest.sha256);
  const sources = await Promise.all(
    assets
      .filter((name) => name.endsWith(".js"))
      .map((name) => readFile(resolve(dist, "assets", name), "utf8")),
  );
  assert.ok(
    sources.some((source) => source.includes(base + "models/active.json")),
    "NNUE manifest must use the deployment base",
  );
}
console.log(
  "Verified " + verified.size + " website assets at " + base + " (including Worker and WASM)",
);
