import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync } from "node:fs";

const base = process.env.GOMOKU_WEB_BASE || "/";

export default defineConfig({
  base,
  define: {
    __GOMOKU_MODEL_MANIFEST__: JSON.stringify(
      existsSync(new URL("./public/models/active.json", import.meta.url))
        ? base + "models/active.json"
        : null,
    ),
  },
  plugins: [react()],
  worker: { format: "es" },
  server: {
    port: 5173,
    strictPort: true,
  },
});
