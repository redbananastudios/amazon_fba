import { describe, it, expect, vi, beforeEach } from "vitest";
import { Cache } from "./cache.js";

describe("Cache", () => {
  let cache: Cache<string>;

  beforeEach(() => {
    cache = new Cache<string>(1000); // 1 second TTL for testing
  });

  it("returns undefined for missing keys", () => {
    expect(cache.get("missing")).toBeUndefined();
  });

  it("stores and retrieves values", () => {
    cache.set("key1", "value1");
    expect(cache.get("key1")).toBe("value1");
  });

  it("expires entries after TTL", () => {
    vi.useFakeTimers();
    cache.set("key1", "value1");
    vi.advanceTimersByTime(1001);
    expect(cache.get("key1")).toBeUndefined();
    vi.useRealTimers();
  });
});
