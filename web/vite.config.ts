import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // File-change events do not cross a Windows or macOS bind mount reliably.
    watch: { usePolling: true, interval: 300 },
    // Same-origin API access in dev; the prod nginx image does the same (see nginx.conf).
    proxy: { "/api": { target: "http://api:8000", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
