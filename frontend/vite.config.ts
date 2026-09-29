import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8094" } },
  test: { environment: "jsdom", setupFiles: ["./vitest.setup.ts"], exclude: ["e2e/**", "node_modules/**"], globals: false, env: { NODE_ENV: "test" } },
});
