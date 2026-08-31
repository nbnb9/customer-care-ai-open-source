import { expect, test, type Page } from "@playwright/test";
import axe from "axe-core";

const CUSTOMER_ID = "user-nw-customer-01";
const AGENT_ID = "user-nw-agent-01";
const SUPERVISOR_ID = "user-nw-supervisor-01";

type AxeViolation = {
  id: string;
  impact: string | null;
  help: string;
  targets: string[][];
};

type AxeRuntime = {
  run: (
    context: Document,
    options: {
      runOnly: { type: "tag"; values: string[] };
      resultTypes: string[];
    },
  ) => Promise<{
    violations: Array<{
      id: string;
      impact: string | null;
      help: string;
      nodes: Array<{ target: string[] }>;
    }>;
  }>;
};

async function openAs(page: Page, userId: string, path: string) {
  await page.goto("/");
  await page.evaluate(
    (selectedUserId: string) =>
      localStorage.setItem("demo-user-id", selectedUserId),
    userId,
  );
  await page.goto(path);
  await page.getByRole("main").waitFor();
}

async function scanWcag22AA(page: Page): Promise<AxeViolation[]> {
  await page.addScriptTag({ content: axe.source });
  return page.evaluate(async () => {
    const runtime = (window as unknown as Window & { axe: AxeRuntime }).axe;
    const results = await runtime.run(document, {
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
      },
      resultTypes: ["violations"],
    });
    return results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target),
    }));
  });
}

async function expectNoDocumentOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth, JSON.stringify(dimensions)).toBeLessThanOrEqual(
    dimensions.clientWidth + 1,
  );
}

const rolePages = [
  { path: "/support", userId: CUSTOMER_ID },
  { path: "/workspace", userId: AGENT_ID },
  { path: "/queue", userId: SUPERVISOR_ID },
];

test("角色首页通过 WCAG 2.2 AA 自动扫描", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });

  for (const rolePage of rolePages) {
    await openAs(page, rolePage.userId, rolePage.path);
    expect(
      await scanWcag22AA(page),
      `${rolePage.path} 存在自动可检测的 WCAG A/AA 违规`,
    ).toEqual([]);
  }
});

test("角色工作台支持跳过导航，并在窄屏和等效 200% 回流下不产生页面级横向滚动", async ({
  page,
}) => {
  for (const rolePage of rolePages) {
    await page.setViewportSize({ width: 1280, height: 900 });
    await openAs(page, rolePage.userId, rolePage.path);

    await page.keyboard.press("Tab");
    const skipLink = page.getByRole("link", { name: "跳到主要内容" });
    await expect(skipLink).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("main")).toBeFocused();

    for (const width of [640, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.reload();
      await page.getByRole("main").waitFor();
      await expectNoDocumentOverflow(page);
    }
  }
});
