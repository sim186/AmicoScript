/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";

// Build output lands in the repo-root frontend/ directory — that's the path
// backend/main.py's FRONTEND_DIR resolves to and serves as static files.
// See CONTEXT.md ("frontend/" is generated output, not source) and
// docs/adr/0002-node-required-for-web-ui-dev.md.
const BACKEND_ORIGIN = "http://127.0.0.1:8002";

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  resolve: {
    alias: {
      $lib: fileURLToPath(new URL("./src/lib", import.meta.url)),
    },
  },
  build: {
    outDir: "../frontend",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      // changeOrigin: the backend's loopback-auth check (backend/auth.py) and
      // CORS allowlist both key off the Host/Origin the request arrives with;
      // without it the proxy forwards the Vite dev origin instead of the
      // backend's own, which those checks don't expect.
      // /api includes the job-progress SSE stream
      // (GET /api/jobs/{id}/stream) — http-proxy pipes chunked responses
      // through unbuffered, so this doesn't need special SSE handling.
      "/api": { target: BACKEND_ORIGIN, changeOrigin: true },
      "/scripts": { target: BACKEND_ORIGIN, changeOrigin: true },
      "/CHANGELOG.md": { target: BACKEND_ORIGIN, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts"],
  },
});
