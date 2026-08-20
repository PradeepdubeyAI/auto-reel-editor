import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Proxy the FastAPI backend so the frontend uses relative /api paths (Range requests
    // for video scrubbing pass straight through — no CORS needed).
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    // Single entry: the mobile flow (mobile.html) -- the desktop editor (index.html) was
    // removed, the mobile app is the only production surface now.
    rollupOptions: {
      input: {
        mobile: path.resolve(__dirname, "mobile.html"),
      },
    },
  },
});
