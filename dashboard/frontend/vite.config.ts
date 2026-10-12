import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/static/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url))
    }
  },
  build: {
    outDir: "../static_dist",
    emptyOutDir: true
  },
  server: {
    port: 5173,
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8080",
        ws: true
      },
      "/api": "http://127.0.0.1:8080",
      "/video-status": "http://127.0.0.1:8080",
      "/webcam-status": "http://127.0.0.1:8080"
    }
  },
  test: {
    environment: "jsdom"
  }
});
