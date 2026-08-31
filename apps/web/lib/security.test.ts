import { describe, expect, it } from "vitest";

import { isSafeKnowledgeSourceLocator } from "./security";

const VALID_SOURCE =
  "/v1/knowledge/chunks/kc-demo-001?product=demo-support&region=CN&language=zh-CN";

describe("isSafeKnowledgeSourceLocator", () => {
  it("accepts a scoped, server-relative knowledge chunk locator", () => {
    expect(isSafeKnowledgeSourceLocator(VALID_SOURCE)).toBe(true);
  });

  it.each([
    "javascript:alert(1)",
    "https://evil.example/v1/knowledge/chunks/kc-demo-001?product=x&region=y&language=z",
    "//evil.example/v1/knowledge/chunks/kc-demo-001?product=x&region=y&language=z",
    "/v1/knowledge/chunks/kc-demo-001",
    "/v1/knowledge/chunks/kc-demo-001?product=demo-support&region=CN",
    "/v1/knowledge/chunks/kc-demo-001/child?product=x&region=y&language=z",
    "/v1/knowledge/chunks/kc-demo%2Fchild?product=x&region=y&language=z",
    "/v1/knowledge/chunks/../secret?product=x&region=y&language=z",
    "/v1/knowledge/chunks/kc-demo-001?product=x&region=y&language=z#fragment",
    "/v1/knowledge/chunks/kc-demo-001?product=x&region=y&language=z\n",
  ])("rejects an unsafe or incomplete locator: %s", (locator) => {
    expect(isSafeKnowledgeSourceLocator(locator)).toBe(false);
  });

  it("rejects non-string and oversized values", () => {
    expect(isSafeKnowledgeSourceLocator(null)).toBe(false);
    expect(isSafeKnowledgeSourceLocator(42)).toBe(false);
    expect(
      isSafeKnowledgeSourceLocator(
        "/v1/knowledge/chunks/" +
          "a".repeat(2_048) +
          "?product=x&region=y&language=z",
      ),
    ).toBe(false);
  });
});
