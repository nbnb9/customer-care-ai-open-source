import { expect, test, type Page } from "@playwright/test";

const AGENT_ID = "user-nw-agent-01";
const W3C_TRACEPARENT = /^00-([0-9a-f]{32})-([0-9a-f]{16})-0[01]$/;

async function openAs(page: Page, userId: string, path: string) {
  await page.goto("/");
  await page.evaluate(
    (selectedUserId: string) =>
      localStorage.setItem("demo-user-id", selectedUserId),
    userId,
  );
  await page.goto(path);
}

test("浏览器 API 请求携带 W3C traceparent，响应可返回同一 Trace ID", async ({
  page,
}) => {
  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/tickets" &&
      response.request().method() === "GET",
  );

  await openAs(page, AGENT_ID, "/workspace");
  const response = await responsePromise;
  const traceparent = response.request().headers()["traceparent"];
  const match = traceparent?.match(W3C_TRACEPARENT);

  expect(match).not.toBeNull();
  expect(response.headers()["x-trace-id"]).toBe(match?.[1]);
  expect(response.headers()["x-request-id"]).toMatch(
    /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/,
  );
});
