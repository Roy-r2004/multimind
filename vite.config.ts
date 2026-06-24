import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = env.BACKEND_PROXY_TARGET || "http://localhost:8001";

  return {
    resolve: {
      tsconfigPaths: true,
    },
    server: {
      proxy: {
        "/api/v1": {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },
    plugins: [
      tailwindcss(),
      tanstackStart({
        server: { entry: "server" },
      }),
      react(),
    ],
  };
});
