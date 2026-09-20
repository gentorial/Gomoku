import { createApp } from "./app.js";
import { WorkerPool, defaultWorkerPath } from "./worker-pool.js";

const app = createApp(
  new WorkerPool(
    process.env.GOMOKU_ENGINE_PATH || defaultWorkerPath,
    Number(process.env.GOMOKU_WORKERS || 2),
  ),
  true,
);
const port = Number(process.env.PORT || 3001);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid PORT");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => {
    void app.close();
  });
}
await app.listen({ host: process.env.HOST || "127.0.0.1", port });
