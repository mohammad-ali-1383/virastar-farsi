import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Development port is fixed at 5173 to match the allowed CORS origins
// documented in context/architecture.md.
//
// The `test` block configures the component tests (Vitest + Testing Library in
// a jsdom environment); `npm run build` still uses the same config for the
// production bundle, and `npm run test` runs the tests once and exits.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
