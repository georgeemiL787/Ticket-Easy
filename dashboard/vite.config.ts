/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The app is served by the Team B service at /dashboard; `npm run build` writes straight into its web folder.
export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  build: { outDir: "../team_b/web/dashboard", emptyOutDir: true },
  server: { proxy: { "/v1": "http://127.0.0.1:8010" } }, // `npm run dev` talks to `make run`
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
