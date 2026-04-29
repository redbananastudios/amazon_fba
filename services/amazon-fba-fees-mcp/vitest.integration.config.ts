import { defineConfig } from "vitest/config";

/**
 * Integration test runner — hits the live SP-API. Tests auto-skip when
 * SP_API_CLIENT_ID is absent, so this config is safe to run anywhere; the
 * tests just won't do anything useful without credentials.
 *
 * Run with:  npm run test:integration
 */
export default defineConfig({
  test: {
    include: ["src/**/*.integration.test.ts"],
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
