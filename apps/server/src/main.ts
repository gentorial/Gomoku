import { readFile } from "node:fs/promises";
import { createApp } from "./app.js";
import { OnlineService } from "./online.js";
import { createReferee } from "./referee.js";
import { WorkerPool, defaultWorkerPath } from "./worker-pool.js";

// Online settings live in config/online.json (a bundle carries a copy named by
// GOMOKU_CONFIG); GOMOKU_ORIGINS overrides the allowed browser origins.
const config = JSON.parse(
  await readFile(
    process.env.GOMOKU_CONFIG || new URL("../../../config/online.json", import.meta.url),
    "utf8",
  ),
) as { origins: string[] };
const origins = process.env.GOMOKU_ORIGINS
  ? process.env.GOMOKU_ORIGINS.split(",")
      .map((origin) => origin.trim())
      .filter(Boolean)
  : config.origins;
// GOMOKU_AI=0 serves online play alone (no native engine); GOMOKU_ONLINE=0 disables it.
const app = createApp(
  process.env.GOMOKU_AI === "0"
    ? null
    : new WorkerPool(
        process.env.GOMOKU_ENGINE_PATH || defaultWorkerPath,
        Number(process.env.GOMOKU_WORKERS || 2),
      ),
  true,
  {
    online:
      process.env.GOMOKU_ONLINE === "0" ? undefined : new OnlineService(await createReferee()),
    origins,
  },
);
const port = Number(process.env.PORT || 3001);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid PORT");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => {
    void app.close();
  });
}
await app.listen({ host: process.env.HOST || "127.0.0.1", port });
