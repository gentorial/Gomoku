import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readFileSync } from "node:fs";

const base = process.env.GOMOKU_WEB_BASE || "/";
const active = new URL("./public/models/active.json", import.meta.url);
const model = existsSync(active) ? JSON.parse(readFileSync(active, "utf8")) : null;
const online = JSON.parse(
  readFileSync(new URL("../../config/online.json", import.meta.url), "utf8"),
);
const onlineUrl: string | null = process.env.GOMOKU_ONLINE_URL || online.url || null;
if (onlineUrl && !/^wss?:\/\//.test(onlineUrl))
  throw new Error("Online url must be ws:// or wss://");

export default defineConfig({
  base,
  define: {
    __GOMOKU_MODEL_MANIFEST__: JSON.stringify(model ? base + "models/active.json" : null),
    // Boards the staged model was trained for; older manifests name one `size`.
    __GOMOKU_MODEL_SIZES__: JSON.stringify(model ? (model.sizes ?? [model.size]) : []),
    // Online play endpoint from config/online.json; without it the 联机 mode is hidden.
    __GOMOKU_ONLINE_URL__: JSON.stringify(onlineUrl),
  },
  plugins: [react()],
  worker: { format: "es" },
  server: {
    port: 5173,
    strictPort: true,
  },
});
