import { describe, expect, it } from "vitest";
import { ago, lifecycleIndex, num, pct, secs } from "./format";

describe("format helpers", () => {
  it("renders missing values as a dash, never as zero", () => {
    expect(pct(null)).toBe("—");
    expect(secs(undefined)).toBe("—");
    expect(num(null, " MB")).toBe("—");
  });
  it("formats measured values", () => {
    expect(pct(0.375)).toBe("38%");
    expect(secs(5.33)).toBe("5.3 s");
    expect(secs(125)).toBe("2m 5s");
  });
  it("maps incident stage to lifecycle step", () => {
    expect(lifecycleIndex("detected", "open")).toBe(0);
    expect(lifecycleIndex("awaiting_approval", "open")).toBe(4);
    expect(lifecycleIndex("verifying", "open")).toBe(5);
    expect(lifecycleIndex("resolved", "resolved")).toBe(6);
    expect(lifecycleIndex("escalated", "escalated")).toBe(6);
  });
  it("computes relative time", () => {
    const now = Date.parse("2026-01-01T00:01:00Z");
    expect(ago("2026-01-01T00:00:30Z", now)).toBe("30s ago");
  });
});
