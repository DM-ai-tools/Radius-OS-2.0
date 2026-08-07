import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Long-running chat SSE (DataForSEO site crawls can take several minutes)
      "/api": {
        target: "http://127.0.0.1:8000",
        timeout: 0,
        proxyTimeout: 0,
      },
      "/health": "http://127.0.0.1:8000",
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
});
