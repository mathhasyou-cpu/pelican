// Две страницы: приложение (index.html) и отчёт для печати (print.html). Сборка — в dist/,
// её отдаёт `pelican.api` с `/`. В разработке `npm run dev` проксирует /api и /r на API
// (по умолчанию http://127.0.0.1:8010, иначе PELICAN_API).
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.PELICAN_API || "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: { main: resolve(import.meta.dirname, "index.html"), print: resolve(import.meta.dirname, "print.html") },
    },
  },
  server: { proxy: { "/api": api } },
  test: { environment: "jsdom", include: ["src/**/*.test.{js,jsx}"] },
});
