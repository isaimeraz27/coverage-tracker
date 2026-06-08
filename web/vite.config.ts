import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built output goes to web/dist, which the Python server serves as static files.
// In dev (`npm run dev`, port 5173) the API + brand + agent endpoints are proxied to
// the live Python server on 8765 so the SPA talks to real data while hot-reloading.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/brand": "http://127.0.0.1:8765",
      "/install.ps1": "http://127.0.0.1:8765",
      "/download": "http://127.0.0.1:8765",
      "/healthz": "http://127.0.0.1:8765",
    },
  },
});
