import { expect, test, type Page } from "@playwright/test";

const AGENT_ID = "user-nw-agent-01";

async function openAs(page: Page, userId: string, path: string) {
  await page.goto("/");
  await page.evaluate(
    (selectedUserId: string) =>
      localStorage.setItem("demo-user-id", selectedUserId),
    userId,
  );
  return page.goto(path);
}

test("恶意模型 HTML 和来源 URL 仅作为文本处理，不能执行或跨域请求", async ({
  page,
}) => {
  const maliciousAnswer =
    '<img src=x onerror="window.__step38Xss=true">安全演练模型输出</img>';
  let externalSourceRequested = false;

  const workspaceResponse = await openAs(page, AGENT_ID, "/workspace");
  expect(workspaceResponse?.headers()["x-content-type-options"]).toBe("nosniff");
  expect(workspaceResponse?.headers()["x-frame-options"]).toBe("DENY");
  await page.evaluate(() => {
    (window as Window & { __step38Xss?: boolean }).__step38Xss = false;
  });
  await page.route("https://evil.example/**", async (route) => {
    externalSourceRequested = true;
    await route.abort();
  });
  await page.route("**/v1/knowledge/answer", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        answer: maliciousAnswer,
        needs_handoff: false,
        uncertainties: [],
        citations: [
          {
            chunk_id: "security-chunk-001",
            document_key: "security-fixture",
            document_version: "1.0.0",
            title: "<script>window.__step38Xss=true</script>",
            excerpt: "<a href=\"javascript:window.__step38Xss=true\">unsafe</a>",
            product: "demo-support",
            region: "cn-north",
            language: "zh-CN",
            effective_at: "2026-01-01T00:00:00Z",
            expires_at: null,
            source_locator: "https://evil.example/steal?token=not-a-capability",
          },
        ],
      }),
    });
  });

  await page
    .getByRole("button")
    .filter({ hasText: "Demo shipment has not arrived" })
    .click();
  await expect(page.getByRole("textbox", { name: "知识问题" })).toBeVisible();
  await page.getByRole("textbox", { name: "知识问题" }).fill("安全演练问题");
  await page.getByRole("button", { name: "基于来源回答" }).click();

  await expect(page.getByText(maliciousAnswer, { exact: true })).toBeVisible();
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(page.getByText("来源（1）", { exact: true })).toBeVisible();
  await page.getByText("来源（1）", { exact: true }).click();
  await expect(
    page.getByText("来源地址不符合受信任的 API 路径。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查看已授权原文片段" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => (window as Window & { __step38Xss?: boolean }).__step38Xss,
    ),
  ).toBe(false);
  expect(externalSourceRequested).toBe(false);
});
