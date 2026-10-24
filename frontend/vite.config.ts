// Build into the Python package (ADR-0030); FastAPI serves it under /app with a strict CSP.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: "../src/newgen_ocr/webapp/dist",
    emptyOutDir: true,
    sourcemap: false,
    // Everything as files: no inline scripts or data: URLs that the CSP would refuse.
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
  // Development only: `npm run dev` proxies the API to a local `make run`.
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom", globals: false, include: ["src/**/*.test.tsx", "src/**/*.test.ts"] },
});
