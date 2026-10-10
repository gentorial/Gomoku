import { createApp } from "./app.js";
import { OnlineService } from "./online.js";
import { createReferee } from "./referee.js";
import { WorkerPool, defaultWorkerPath } from "./worker-pool.js";

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
    origins: process.env.GOMOKU_ORIGINS?.split(",")
      .map((origin) => origin.trim())
      .filter(Boolean),
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
