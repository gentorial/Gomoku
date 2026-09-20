import { createHash } from "node:crypto";
import { access, copyFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { root } from "./process.mjs";

// Pin a model independently of the website bundle. CI downloads it at build time;
// the published website serves it from its own origin (including on Pages).
export async function stageModel({ download = false } = {}) {
  const pin = JSON.parse(await readFile(resolve(root, "models/web-model.json"), "utf8"));
  if (!/^[a-z0-9-]+$/.test(pin.id) || !/^[a-f0-9]{64}$/.test(pin.sha256))
    throw new Error("Invalid web model pin");
  const directory = resolve(root, "apps/web/public/models", pin.sha256);
  const target = resolve(directory, "weights.gnn");
  const local = resolve(root, "artifacts/models", pin.id, "weights.gnn");
  async function valid(path) {
    const bytes = await readFile(path).catch((error) => {
      if (error.code === "ENOENT") return null;
      throw error;
    });
    if (!bytes) return false;
    if (
      bytes.length !== pin.bytes ||
      createHash("sha256").update(bytes).digest("hex") !== pin.sha256
    )
      throw new Error("Model checksum/size mismatch: " + path);
    return true;
  }
  if (!(await valid(target))) {
    await mkdir(directory, { recursive: true });
    if (await valid(local)) await copyFile(local, target);
    else if (download) {
      const response = await fetch(process.env.GOMOKU_MODEL_URL || pin.url, {
        signal: AbortSignal.timeout(300000),
      });
      if (!response.ok) throw new Error("Model download failed: HTTP " + response.status);
      const bytes = Buffer.from(await response.arrayBuffer());
      if (
        bytes.length !== pin.bytes ||
        createHash("sha256").update(bytes).digest("hex") !== pin.sha256
      )
        throw new Error("Downloaded model checksum/size mismatch");
      const temporary = target + ".tmp";
      await writeFile(temporary, bytes);
      await rename(temporary, target);
    } else {
      console.log("NNUE asset not staged. Run pnpm model:download to enable the trained AI.");
      return false;
    }
  }
  const { url: _url, ...manifest } = pin;
  await writeFile(
    resolve(root, "apps/web/public/models/active.json"),
    JSON.stringify(
      { ...manifest, formatVersion: 1, weights: pin.sha256 + "/weights.gnn" },
      null,
      2,
    ) + "\n",
  );
  await access(target);
  console.log("Verified browser NNUE: " + pin.id + " (" + pin.bytes + " bytes)");
  return true;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(root, "scripts/model.mjs")) {
  try {
    await stageModel({ download: process.argv.includes("--download") });
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
