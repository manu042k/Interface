import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The gateway runs on :8080 (`cua serve`). Proxy API calls in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
