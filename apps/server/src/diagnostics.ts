import { appendFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const directory =
  process.env.GOMOKU_LOG_DIR || fileURLToPath(new URL("../../../logs", import.meta.url));
export function logServer(event: string, fields: Record<string, unknown> = {}, level = "info") {
  const configured = process.env.GOMOKU_LOG_LEVEL || "info";
  if (configured === "off" || (level === "debug" && configured !== "debug")) return;
  try {
    const date = new Date();
    const record = JSON.stringify({
      ...fields,
      timestampMs: date.getTime(),
      component: "server",
      level,
      event,
    });
    mkdirSync(directory, { recursive: true });
    appendFileSync(
      resolve(directory, "server-" + date.toISOString().slice(0, 10) + ".jsonl"),
      record + "\n",
    );
  } catch {
    /* File errors must not fail requests. */
  }
}
