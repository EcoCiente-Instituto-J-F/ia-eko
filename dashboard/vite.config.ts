import { defineConfig } from "vitest/config";

// A API do ia-eko não habilita CORS; em desenvolvimento o Vite repassa as rotas
// de observabilidade para ela. Troque o alvo com VITE_API_URL.
const alvo = process.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/metrics": alvo,
      "/health": alvo,
      "/api": alvo,
    },
  },
  preview: {
    port: 5173,
    proxy: {
      "/metrics": alvo,
      "/health": alvo,
      "/api": alvo,
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
