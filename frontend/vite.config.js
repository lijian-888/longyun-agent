import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://api:8000",
        changeOrigin: true,
      },
      "/acps": {
        target: "http://api:8000",
        changeOrigin: true,
      },
      "/.well-known/acps-agent.json": {
        target: "http://api:8000",
        changeOrigin: true,
      },
    },
  },
});
