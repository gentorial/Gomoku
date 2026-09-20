import { run } from "./process.mjs";
import { buildWasm } from "./wasm.mjs";

const task = process.argv[2];
async function engine(preset = "dev") {
  await run("cmake", ["--preset", preset]);
  await run("cmake", ["--build", "--preset", preset, "--parallel", "4"]);
}
async function typescript() {
  await run("pnpm", ["-r", "build"]);
}
async function tests() {
  await run("ctest", ["--preset", "dev"]);
  await run("pnpm", ["test:ts"]);
  await run(process.env.PYTHON || "python", [
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests/python",
    "-v",
  ]);
}
try {
  if (task === "engine" || task === "competition") {
    await engine(task === "competition" ? "competition" : "dev");
  } else if (task === "build" || task === "check" || task === "test") {
    await engine();
    await buildWasm();
    await typescript();
    if (task !== "build") await tests();
    if (task === "check") {
      await run("pnpm", ["typecheck"]);
      await run("pnpm", ["format:check"]);
    }
  } else {
    throw new Error("Unknown task: " + task);
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
