import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the browser talks to Vite on :5173 and Vite forwards /api and /catalog
// to the two FastAPI services. Same origin for the browser, so no CORS setup needed.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const searchTarget = env.FASHIONQ_API || "http://localhost:8000";
  const catalogTarget = env.FASHIONQ_CATALOG || "http://localhost:8001";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target: searchTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
        "/catalog": { target: catalogTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/catalog/, "") },
      },
    },
  };
});
