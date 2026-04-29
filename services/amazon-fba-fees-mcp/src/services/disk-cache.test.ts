import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { DiskCache, loadTtls } from "./disk-cache.js";

describe("DiskCache", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "disk-cache-test-"));
  });

  afterEach(() => {
    if (existsSync(tmp)) rmSync(tmp, { recursive: true, force: true });
  });

  it("returns hit=false for missing key", () => {
    const c = new DiskCache<{ x: number }>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    const r = c.get("missing");
    expect(r.hit).toBe(false);
    expect(r.stale).toBe(false);
    expect(r.data).toBeUndefined();
  });

  it("set then get returns same value within TTL", () => {
    const c = new DiskCache<{ x: number }>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    c.set(["asin", "B001"], { data: { x: 42 } });
    const r = c.get("asin", "B001");
    expect(r.hit).toBe(true);
    expect(r.data).toEqual({ x: 42 });
  });

  it("creates a JSON file with fetched_at + ttl_seconds + data", () => {
    const c = new DiskCache<string>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    c.set(["k1"], { data: "hello" });
    const path = join(tmp, "test", "k1.json");
    expect(existsSync(path)).toBe(true);
    const entry = JSON.parse(readFileSync(path, "utf8"));
    expect(entry.data).toBe("hello");
    expect(entry.ttl_seconds).toBe(60);
    expect(typeof entry.fetched_at).toBe("string");
    expect(Date.parse(entry.fetched_at)).not.toBeNaN();
  });

  it("returns stale=true for expired entry but still returns data", () => {
    const c = new DiskCache<string>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    // Manually write an entry that's already expired (fetched 2 hours ago, TTL 60s)
    const path = join(tmp, "test");
    require("fs").mkdirSync(path, { recursive: true });
    const expired = {
      fetched_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      ttl_seconds: 60,
      data: "old",
    };
    writeFileSync(join(path, "k1.json"), JSON.stringify(expired));
    const r = c.get("k1");
    expect(r.hit).toBe(false);
    expect(r.stale).toBe(true);
    expect(r.data).toBe("old");
  });

  it("respects per-call TTL override on set", () => {
    const c = new DiskCache<string>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    c.set(["short"], { data: "v", ttlSeconds: 1 }); // 1 second TTL
    expect(c.get("short").hit).toBe(true);
    // Manually backdate file
    const path = join(tmp, "test", "short.json");
    const entry = JSON.parse(readFileSync(path, "utf8"));
    entry.fetched_at = new Date(Date.now() - 5000).toISOString();
    writeFileSync(path, JSON.stringify(entry));
    const r = c.get("short");
    expect(r.hit).toBe(false);
    expect(r.stale).toBe(true);
    expect(r.data).toBe("v");
  });

  it("sanitises unsafe characters in key parts", () => {
    const c = new DiskCache<string>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    // forward slash, colon, pipe — all unsafe filename chars
    c.set(["a/b", "c:d", "e|f"], { data: "ok" });
    const r = c.get("a/b", "c:d", "e|f");
    expect(r.hit).toBe(true);
    expect(r.data).toBe("ok");
  });

  it("treats corrupt files as a miss", () => {
    const c = new DiskCache<string>({
      resource: "test",
      defaultTtlSeconds: 60,
      cacheRoot: tmp,
    });
    require("fs").mkdirSync(join(tmp, "test"), { recursive: true });
    writeFileSync(join(tmp, "test", "k1.json"), "{not valid json");
    const r = c.get("k1");
    expect(r.hit).toBe(false);
    expect(r.stale).toBe(false);
  });
});

describe("loadTtls", () => {
  it("returns spec defaults when no env vars set", () => {
    const ttls = loadTtls({});
    expect(ttls.restrictions).toBe(7 * 24 * 60 * 60);
    expect(ttls.fbaEligibility).toBe(7 * 24 * 60 * 60);
    expect(ttls.catalog).toBe(30 * 24 * 60 * 60);
    expect(ttls.fees).toBe(24 * 60 * 60);
    expect(ttls.pricing).toBe(5 * 60);
  });

  it("honours env-var overrides", () => {
    const ttls = loadTtls({
      MCP_CACHE_TTL_RESTRICTIONS_S: "100",
      MCP_CACHE_TTL_PRICING_S: "30",
    });
    expect(ttls.restrictions).toBe(100);
    expect(ttls.pricing).toBe(30);
    expect(ttls.fees).toBe(24 * 60 * 60); // unchanged default
  });

  it("ignores non-numeric overrides", () => {
    const ttls = loadTtls({ MCP_CACHE_TTL_FEES_S: "not a number" });
    expect(ttls.fees).toBe(24 * 60 * 60);
  });

  it("ignores zero/negative overrides", () => {
    const ttls = loadTtls({
      MCP_CACHE_TTL_FEES_S: "0",
      MCP_CACHE_TTL_CATALOG_S: "-1",
    });
    expect(ttls.fees).toBe(24 * 60 * 60);
    expect(ttls.catalog).toBe(30 * 24 * 60 * 60);
  });
});
