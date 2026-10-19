import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { CORE_API_BASE, PROBLEM_MEDIA_TYPE } from "../src/index";

describe("api-client", () => {
  it("is generated from the frozen core spec", () => {
    const core = readFileSync(new URL("../src/generated/core.ts", import.meta.url), "utf8");
    expect(core).toContain('"/api/v1/agent/proposals"');
    expect(core).toContain('"/healthz"');
  });

  it("exposes constants used by the console", () => {
    expect(CORE_API_BASE).toBe("/api/v1");
    expect(PROBLEM_MEDIA_TYPE).toBe("application/problem+json");
  });
});
