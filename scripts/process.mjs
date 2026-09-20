import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

export const root = fileURLToPath(new URL("../", import.meta.url));
export function command(executable, args, options = {}) {
  // pnpm is already running this script: invoke its JS entry without a shell.
  if (executable === "pnpm" && process.env.npm_execpath) {
    args = [process.env.npm_execpath, ...args];
    executable = process.execPath;
  }
  return spawn(executable, args, {
    cwd: root,
    stdio: "inherit",
    windowsHide: true,
    ...options,
  });
}
export function run(executable, args) {
  return new Promise((resolve, reject) => {
    const child = command(executable, args);
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(executable + " exited with " + code)),
    );
  });
}
export function stop(child) {
  if (!child?.pid || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }
}
