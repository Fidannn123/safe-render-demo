import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// No CDN, no external fonts: everything is bundled (reviewers may be in mainland China).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", assetsInlineLimit: 0, sourcemap: false },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: { environment: "node", include: ["test/**/*.test.{ts,tsx}"] },
});
