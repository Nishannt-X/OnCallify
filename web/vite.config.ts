import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the agent API runs on :7080; the built app is served by the agent itself.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, target: "es2020" },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:7080" } },
  test: { environment: "jsdom", include: ["test/**/*.test.tsx"] },
} as never);
