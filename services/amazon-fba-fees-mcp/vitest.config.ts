import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    // Integration tests live in src/__integration__/ and are gated on real
    // SP-API credentials. Run them separately via `npm run test:integration`.
    exclude: ["**/__integration__/**", "**/node_modules/**"],
  },
});
