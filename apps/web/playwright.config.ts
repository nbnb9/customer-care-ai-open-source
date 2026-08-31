import {
  defineConfig,
  devices,
  type PlaywrightTestConfig,
} from "@playwright/test";
import { resolve } from "node:path";

const webBaseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3100";
const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:8100";
const reuseExistingServer = process.env.E2E_REUSE_EXISTING_SERVER === "true";
const configDir = __dirname;

const webServers: NonNullable<PlaywrightTestConfig["webServer"]> = [];

if (process.env.E2E_START_API_SERVER !== "false") {
  const apiUrl = new URL(apiBaseUrl);
  if (
    apiUrl.protocol !== "http:" ||
    !["localhost", "127.0.0.1", "::1"].includes(apiUrl.hostname)
  ) {
    throw new Error(
      "自动启动的 E2E API 必须使用本机 HTTP 地址；如需复用外部测试环境，请显式设置 E2E_START_API_SERVER=false。",
    );
  }
  const apiPort = apiUrl.port || "80";
  webServers.push({
    name: "CustomerCare API",
    command:
      process.env.E2E_API_COMMAND ??
      `uv run python -m app.e2e_server --host 127.0.0.1 --port ${apiPort}`,
    cwd: resolve(configDir, "../api"),
    env: {
      APP_ENV: "test",
      ALLOW_DEMO_IDENTITY: "true",
      CUSTOMER_TOOLS_PROVIDER: "mock",
      LLM_PROVIDER: "fake",
      CORS_ALLOWED_ORIGINS: new URL(webBaseUrl).origin,
      E2E_DATABASE_URL: process.env.E2E_DATABASE_URL ?? "",
    },
    url: `${apiBaseUrl}/ready`,
    reuseExistingServer,
    timeout: 120_000,
  });
}

if (process.env.E2E_START_WEB_SERVER !== "false") {
  const webUrl = new URL(webBaseUrl);
  if (
    webUrl.protocol !== "http:" ||
    !["localhost", "127.0.0.1", "::1"].includes(webUrl.hostname)
  ) {
    throw new Error("自动启动的 E2E Web 必须使用本机 HTTP 地址。");
  }
  const webPort = webUrl.port || "80";
  webServers.push({
    name: "CustomerCare Web",
    command: process.env.E2E_WEB_COMMAND ?? `pnpm dev --port ${webPort}`,
    env: { NEXT_PUBLIC_API_BASE_URL: apiBaseUrl },
    url: webBaseUrl,
    reuseExistingServer,
    timeout: 120_000,
  });
}

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: webBaseUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: webServers.length ? webServers : undefined,
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
