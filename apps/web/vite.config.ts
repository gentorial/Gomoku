import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readFileSync } from "node:fs";

const base = process.env.GOMOKU_WEB_BASE || "/";
const active = new URL("./public/models/active.json", import.meta.url);
const model = existsSync(active) ? JSON.parse(readFileSync(active, "utf8")) : null;

export default defineConfig({
  base,
  define: {
    __GOMOKU_MODEL_MANIFEST__: JSON.stringify(model ? base + "models/active.json" : null),
    // Boards the staged model was trained for; older manifests name one `size`.
    __GOMOKU_MODEL_SIZES__: JSON.stringify(model ? (model.sizes ?? [model.size]) : []),
  },
  plugins: [react()],
  worker: { format: "es" },
  server: {
    port: 5173,
    strictPort: true,
  },
});
