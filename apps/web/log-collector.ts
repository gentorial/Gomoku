import { appendFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

export function logCollector(base: string): Plugin {
  const directory =
    process.env.GOMOKU_LOG_DIR || fileURLToPath(new URL("../../logs", import.meta.url));
  return {
    name: "gomoku-local-logs",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(base + "__gomoku_logs", async (request, response) => {
        const origin = request.headers.origin;
        let foreignOrigin = false;
        try {
          foreignOrigin = !!origin && new URL(origin).host !== request.headers.host;
        } catch {
          foreignOrigin = true;
        }
        if (
          request.method !== "POST" ||
          request.headers["sec-fetch-site"] === "cross-site" ||
          foreignOrigin ||
          !request.headers["content-type"]?.startsWith("application/json")
        ) {
          response.statusCode = 403;
          response.end();
          return;
        }
        try {
          const chunks: Buffer[] = [];
          let bytes = 0;
          for await (const chunk of request) {
            bytes += Buffer.byteLength(chunk);
            if (bytes > 262144) {
              response.statusCode = 413;
              response.end();
              return;
            }
            chunks.push(Buffer.from(chunk));
          }
          const records: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (
            !Array.isArray(records) ||
            records.length > 256 ||
            records.some(
              (record) =>
                !record ||
                typeof record !== "object" ||
                typeof record.event !== "string" ||
                typeof record.component !== "string" ||
                !Number.isFinite(record.timestampMs),
            )
          ) {
            response.statusCode = 400;
            response.end();
            return;
          }
          const date = new Date().toISOString().slice(0, 10);
          await mkdir(directory, { recursive: true });
          if (records.length)
            await appendFile(
              resolve(directory, "gui-" + date + ".jsonl"),
              records.map((record) => JSON.stringify(record)).join("\n") + "\n",
              "utf8",
            );
          response.statusCode = 204;
          response.end();
        } catch {
          response.statusCode = 500;
          response.end();
        }
      });
    },
  };
}
