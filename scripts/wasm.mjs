import { access, mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { command, root } from "./process.mjs";

export const sdkVersion = "4.0.23";
const sdkCommit = "c0bb220cb6e6f4e0fabb6f6db9efd53390ef5e56";
const sdk = resolve(root, ".tools/emsdk");
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
async function run(executable, args, options = {}) {
  await new Promise((accept, reject) => {
    const child = command(executable, args, options);
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0 ? accept() : reject(new Error(executable + " exited with " + code)),
    );
  });
}
export async function setupWasm() {
  await mkdir(resolve(root, ".tools"), { recursive: true });
  try {
    await access(resolve(sdk, "emsdk.py"));
  } catch {
    await run("git", [
      "clone",
      "--depth",
      "1",
      "--branch",
      sdkVersion,
      "https://github.com/emscripten-core/emsdk.git",
      sdk,
    ]);
  }
  const head = (await readFile(resolve(sdk, ".git/HEAD"), "utf8")).trim();
  if (head !== sdkCommit) throw new Error("Unexpected local emsdk revision; expected " + sdkCommit);
  await run(python, [resolve(sdk, "emsdk.py"), "install", sdkVersion], { cwd: sdk });
  await run(python, [resolve(sdk, "emsdk.py"), "activate", sdkVersion], { cwd: sdk });
}
export async function buildWasm() {
  const emscripten = process.env.EMSDK
    ? resolve(process.env.EMSDK, "upstream/emscripten")
    : resolve(sdk, "upstream/emscripten");
  const driver = resolve(emscripten, "emcmake.py");
  await access(driver).catch(() => {
    throw new Error("Install Emscripten first: pnpm wasm:setup");
  });
  await run(python, [driver, "cmake", "--preset", "wasm", "-G", "Ninja"]);
  await run("cmake", ["--build", "--preset", "wasm", "--parallel", "4"]);
}
if (process.argv[1] && resolve(process.argv[1]) === resolve(root, "scripts/wasm.mjs")) {
  try {
    if (process.argv[2] === "setup") await setupWasm();
    else await buildWasm();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
