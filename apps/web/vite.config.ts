import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync } from "node:fs";
import { logCollector } from "./log-collector.js";

const base = process.env.GOMOKU_WEB_BASE || "/";

export default defineConfig({
  base,
  define: {
    __GOMOKU_LOG_LEVEL__: JSON.stringify(
      ["off", "info", "debug"].includes(process.env.GOMOKU_LOG_LEVEL || "")
        ? process.env.GOMOKU_LOG_LEVEL
        : "info",
    ),
    __GOMOKU_MODEL_MANIFEST__: JSON.stringify(
      existsSync(new URL("./public/models/active.json", import.meta.url))
        ? base + "models/active.json"
        : null,
    ),
  },
  plugins: [react(), logCollector(base)],
  worker: { format: "es" },
  server: {
    port: 5173,
    strictPort: true,
  },
});
