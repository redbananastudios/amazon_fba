import { describe, it, expect } from "vitest";
import { parseArgs } from "./cli.js";

describe("parseArgs", () => {
  it("parses flags with values", () => {
    const r = parseArgs(["preflight", "--input", "items.json", "--seller-id", "S1"]);
    expect(r.positional).toEqual(["preflight"]);
    expect(r.flags).toEqual({ input: "items.json", "seller-id": "S1" });
  });

  it("parses boolean flags (no value follows)", () => {
    const r = parseArgs(["preflight", "--input", "-", "--refresh-cache", "--pretty"]);
    expect(r.flags).toEqual({
      input: "-",
      "refresh-cache": true,
      pretty: true,
    });
  });

  it("treats consecutive --flags as boolean for the first", () => {
    const r = parseArgs(["fees", "--refresh-cache", "--input", "items.json"]);
    expect(r.flags["refresh-cache"]).toBe(true);
    expect(r.flags["input"]).toBe("items.json");
  });

  it("parses comma-separated lists in --asins", () => {
    const r = parseArgs(["restrictions", "--asins", "B001,B002,B003", "--seller-id", "S1"]);
    expect(r.flags["asins"]).toBe("B001,B002,B003");
  });

  it("supports --input - for stdin", () => {
    const r = parseArgs(["preflight", "--input", "-"]);
    expect(r.flags["input"]).toBe("-");
  });

  it("preserves positional args order", () => {
    const r = parseArgs(["preflight", "--input", "items.json", "extra", "args"]);
    expect(r.positional).toEqual(["preflight", "extra", "args"]);
  });

  it("returns empty positional and empty flags for no args", () => {
    const r = parseArgs([]);
    expect(r.positional).toEqual([]);
    expect(r.flags).toEqual({});
  });

  it("handles --help as a boolean flag", () => {
    const r = parseArgs(["--help"]);
    expect(r.flags["help"]).toBe(true);
    expect(r.positional).toEqual([]);
  });

  it("treats `--` as a stop token; everything after is positional", () => {
    const r = parseArgs([
      "preflight",
      "--refresh-cache",
      "--",
      "--not-a-flag",
      "literal-arg",
    ]);
    expect(r.flags["refresh-cache"]).toBe(true);
    expect(r.positional).toEqual(["preflight", "--not-a-flag", "literal-arg"]);
  });

  it("supports --key=value form without greedy lookahead", () => {
    const r = parseArgs(["preflight", "--input=items.json", "--refresh-cache=true"]);
    expect(r.flags["input"]).toBe("items.json");
    expect(r.flags["refresh-cache"]).toBe("true");
  });

  it("--key=value lets a boolean flag precede a positional safely", () => {
    // The greedy lookahead form would consume "extra" as the value of
    // --refresh-cache; --refresh-cache=true is the unambiguous escape.
    const r = parseArgs(["fees", "--refresh-cache=true", "extra"]);
    expect(r.flags["refresh-cache"]).toBe("true");
    expect(r.positional).toEqual(["fees", "extra"]);
  });
});
