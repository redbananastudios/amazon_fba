import { existsSync, mkdirSync, readFileSync, writeFileSync, statSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * Walk up from `start` until a directory containing `.git` is found.
 * Returns the directory path, or undefined if no marker is found.
 * Handles git worktrees: `.git` may be a file (pointing to gitdir) or a directory.
 */
function findRepoRoot(start: string): string | undefined {
  let dir = resolve(start);
  while (true) {
    if (existsSync(join(dir, ".git"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

/**
 * Default cache root: <repo>/.cache/fba-mcp/, falling back to <cwd>/.cache/fba-mcp/
 * if no repo marker is found.
 */
function defaultCacheRoot(): string {
  const repoRoot = findRepoRoot(__dirname);
  const base = repoRoot ?? process.cwd();
  return join(base, ".cache", "fba-mcp");
}

export interface DiskCacheEntry<T> {
  fetched_at: string;       // ISO-8601 timestamp
  ttl_seconds: number;       // entry-specific TTL
  data: T;
}

export interface DiskCacheGetResult<T> {
  hit: boolean;
  stale: boolean;            // true when entry exists but is past TTL
  fetched_at?: string;
  data?: T;
}

export interface DiskCacheOptions {
  resource: string;          // subdirectory under cache root: e.g. "restrictions"
  defaultTtlSeconds: number;
  cacheRoot?: string;        // override for tests; defaults to repo .cache/fba-mcp/
}

export interface DiskCacheSetOptions<T> {
  data: T;
  ttlSeconds?: number;
}

/**
 * Persistent disk cache. Layout:
 *   <cacheRoot>/<resource>/<keyParts...>.json
 * Each entry stores fetched_at + ttl_seconds + data so the TTL can vary per call.
 *
 * On miss/expired: get() returns { hit: false }. The caller fetches fresh data
 * and writes via set().
 *
 * On miss-but-stale-exists: get() returns { hit: false, stale: true, data, fetched_at }.
 * Useful for the "serve stale on SP-API error" fallback — the caller can choose
 * to use stale data when the upstream fails.
 */
export class DiskCache<T> {
  private readonly resourceDir: string;
  private readonly defaultTtlSeconds: number;

  constructor(options: DiskCacheOptions) {
    const root = options.cacheRoot ?? defaultCacheRoot();
    this.resourceDir = join(root, options.resource);
    this.defaultTtlSeconds = options.defaultTtlSeconds;
  }

  private keyToPath(keyParts: string[]): string {
    // Sanitise key parts to safe filesystem segments.
    const safe = keyParts.map((p) => p.replace(/[^A-Za-z0-9._-]/g, "_"));
    const file = safe.join("__") + ".json";
    return join(this.resourceDir, file);
  }

  private ensureDir(filePath: string): void {
    const d = dirname(filePath);
    if (!existsSync(d)) mkdirSync(d, { recursive: true });
  }

  /**
   * Get an entry. Returns hit=true only if the entry exists and is within TTL.
   * If expired: hit=false, stale=true, with the stale data also returned so the
   * caller can choose to use it (e.g. on upstream error).
   */
  get(...keyParts: string[]): DiskCacheGetResult<T> {
    const path = this.keyToPath(keyParts);
    if (!existsSync(path)) return { hit: false, stale: false };
    let entry: DiskCacheEntry<T>;
    try {
      entry = JSON.parse(readFileSync(path, "utf8")) as DiskCacheEntry<T>;
    } catch {
      // Corrupt file — treat as miss.
      return { hit: false, stale: false };
    }
    const fetchedAtMs = Date.parse(entry.fetched_at);
    if (Number.isNaN(fetchedAtMs)) return { hit: false, stale: false };
    const expiresAtMs = fetchedAtMs + entry.ttl_seconds * 1000;
    const stale = Date.now() >= expiresAtMs;
    if (stale) {
      return { hit: false, stale: true, fetched_at: entry.fetched_at, data: entry.data };
    }
    return { hit: true, stale: false, fetched_at: entry.fetched_at, data: entry.data };
  }

  /**
   * Write an entry. keyParts is an array (matches the way callers
   * already build cache keys); the second arg is an options object so
   * data and ttl are named at the call site:
   *   cache.set(cacheKey, { data: result });
   *   cache.set(["k1", "k2"], { data: result, ttlSeconds: 60 });
   */
  set(keyParts: string[], opts: DiskCacheSetOptions<T>): void {
    const path = this.keyToPath(keyParts);
    this.ensureDir(path);
    const entry: DiskCacheEntry<T> = {
      fetched_at: new Date().toISOString(),
      ttl_seconds: opts.ttlSeconds ?? this.defaultTtlSeconds,
      data: opts.data,
    };
    writeFileSync(path, JSON.stringify(entry, null, 2), "utf8");
  }

  /** Inspect age of an existing entry without affecting it. Useful for tests/diagnostics. */
  ageSeconds(...keyParts: string[]): number | undefined {
    const path = this.keyToPath(keyParts);
    if (!existsSync(path)) return undefined;
    const stat = statSync(path);
    return Math.floor((Date.now() - stat.mtimeMs) / 1000);
  }
}

/**
 * TTL configuration loader. Reads env-var overrides, falls back to spec defaults.
 */
export interface CacheTtls {
  restrictions: number;
  fbaEligibility: number;
  catalog: number;
  fees: number;
  pricing: number;
}

export function loadTtls(env: NodeJS.ProcessEnv = process.env): CacheTtls {
  const num = (key: string, fallback: number): number => {
    const v = env[key];
    if (!v) return fallback;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : fallback;
  };
  return {
    restrictions: num("MCP_CACHE_TTL_RESTRICTIONS_S", 7 * 24 * 60 * 60), // 7d
    fbaEligibility: num("MCP_CACHE_TTL_FBA_S", 7 * 24 * 60 * 60),         // 7d
    catalog: num("MCP_CACHE_TTL_CATALOG_S", 30 * 24 * 60 * 60),           // 30d
    fees: num("MCP_CACHE_TTL_FEES_S", 24 * 60 * 60),                       // 24h
    pricing: num("MCP_CACHE_TTL_PRICING_S", 5 * 60),                       // 5min
  };
}
