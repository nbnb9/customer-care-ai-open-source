import { expect, test, type Page } from "@playwright/test";

const CUSTOMER_ID = "user-nw-customer-01";
const AGENT_ID = "user-nw-agent-01";
const SECOND_AGENT_ID = "user-nw-agent-02";
const SUPERVISOR_ID = "user-nw-supervisor-01";

async function openAs(page: Page, userId: string, path: string) {
  await page.goto("/");
  await page.evaluate(
    (selectedUserId: string) =>
      localStorage.setItem("demo-user-id", selectedUserId),
    userId,
  );
  await page.goto(path);
}

async function createTicket(
  page: Page,
  subject: string,
  description = "端到端测试工单描述",
) {
  await openAs(page, CUSTOMER_ID, "/support");
  await page.getByPlaceholder("主题").fill(subject);
  await page.getByPlaceholder("描述").fill(description);
  const createButton = page.getByRole("button", { name: "创建工单" });
  await expect(createButton).toBeEnabled();
  await createButton.click();
  await expect(page.getByRole("status")).toContainText("工单已创建");
  await expect(
    page.getByRole("link", { name: subject, exact: true }),
  ).toBeVisible();
}

async function selectAgentTicket(page: Page, subject: string) {
  const queueItem = page
    .getByRole("button")
    .filter({ hasText: subject })
    .first();
  await expect(queueItem).toBeVisible();
  await queueItem.click();
  await expect(
    page.getByRole("heading", { name: subject, exact: true }),
  ).toBeVisible();
}

test("客户建单，坐席领取、回复和更新状态后客户可确认", async ({ page }) => {
  const subject = `E2E 客户旅程 ${Date.now()}`;
  const reply = "已收到问题，我们正在处理。";

  await createTicket(page, subject);

  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);

  await page.getByRole("combobox", { name: "工单状态" }).selectOption("triaged");
  await page.getByRole("button", { name: "更新状态" }).click();
  await expect(page.getByRole("status")).toContainText("状态已更新为“已分诊”");

  await page.getByRole("button", { name: "领取工单" }).click();
  await expect(page.getByRole("status")).toContainText("工单已领取");

  await page.getByPlaceholder("客户可见回复").fill(reply);
  await page.getByRole("button", { name: "发送客户可见回复" }).click();
  await expect(page.getByRole("status")).toContainText("客户可见回复已发送");

  await page.getByRole("combobox", { name: "工单状态" }).selectOption("in_progress");
  await page.getByRole("button", { name: "更新状态" }).click();
  await expect(page.getByRole("status")).toContainText("状态已更新为“处理中”");

  await openAs(page, CUSTOMER_ID, "/support");
  await page.getByRole("link", { name: subject, exact: true }).click();
  await expect(page.getByText(/状态：处理中/)).toBeVisible();
  await expect(page.getByText(reply, { exact: true })).toBeVisible();
});

test("主管可将同租户已分配工单重新分配给另一坐席", async ({ page }) => {
  const subject = `E2E 主管重分配 ${Date.now()}`;

  await createTicket(page, subject);
  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);
  await page.getByRole("combobox", { name: "工单状态" }).selectOption("triaged");
  await page.getByRole("button", { name: "更新状态" }).click();
  await expect(page.getByRole("status")).toContainText("状态已更新为“已分诊”");
  await page.getByRole("button", { name: "领取工单" }).click();
  await expect(page.getByRole("status")).toContainText("工单已领取");

  await openAs(page, SUPERVISOR_ID, "/queue");
  const ticketRow = page.getByRole("row").filter({ hasText: subject });
  await expect(ticketRow).toBeVisible();
  await ticketRow.getByRole("combobox").selectOption(SECOND_AGENT_ID);
  await ticketRow.getByRole("button", { name: "重新分配" }).click();
  await expect(page.getByRole("status")).toContainText(
    "已分配给 Northwind 坐席 02",
  );
  await expect(ticketRow).toContainText("Northwind 坐席 02");
});

test("分诊摘要编辑失败时保留草稿，保存后刷新仍可见", async ({ page }) => {
  const subject = `E2E 分诊编辑 ${Date.now()}`;
  const editedSummary = "坐席补充后的准确问题摘要";

  await createTicket(page, subject, "订单物流长时间没有更新");
  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);

  await page.getByRole("button", { name: "生成建议" }).click();
  await expect(page.getByText("AI 建议 · 尚未处理", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("textbox", { name: "编辑建议摘要" }).fill(editedSummary);

  await page.route(
    "**/v1/tickets/**/triage/decision",
    async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "模拟网络失败" }),
      });
    },
    { times: 1 },
  );
  await page.getByRole("button", { name: "保存编辑" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "模拟网络失败" }),
  ).toContainText("模拟网络失败");
  await expect(
    page.getByRole("textbox", { name: "编辑建议摘要" }),
  ).toHaveValue(editedSummary);

  await page.getByRole("button", { name: "保存编辑" }).click();
  await expect(page.getByText("坐席已编辑", { exact: true })).toBeVisible();
  await expect(page.getByText(`坐席编辑结果：${editedSummary}`)).toBeVisible();

  await page.reload();
  await selectAgentTicket(page, subject);
  await expect(page.getByText("坐席已编辑", { exact: true })).toBeVisible();
  await expect(page.getByText(`坐席编辑结果：${editedSummary}`)).toBeVisible();
});

test("坐席拒绝分诊建议并在刷新后看到原因", async ({ page }) => {
  const subject = `E2E 分诊拒绝 ${Date.now()}`;

  await createTicket(page, subject, "问题描述缺少订单上下文");
  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);

  await page.getByRole("button", { name: "生成建议" }).click();
  await page
    .getByRole("combobox", { name: "拒绝原因" })
    .selectOption("missing_context");
  await page.getByRole("button", { name: "拒绝建议" }).click();
  await expect(page.getByText("坐席已拒绝", { exact: true })).toBeVisible();
  await expect(page.getByText(/原因：上下文不足/)).toBeVisible();

  await page.reload();
  await selectAgentTicket(page, subject);
  await expect(page.getByText("坐席已拒绝", { exact: true })).toBeVisible();
  await expect(page.getByText(/原因：上下文不足/)).toBeVisible();
});

test("客户 R0 会话只展示公开引用、可复核原文并记录反馈", async ({ page }) => {
  const conversationId = "conversation-e2e-r0";
  const sourceLocator =
    "/v1/knowledge/chunks/knowledge-chunk-public-e2e?product=demo-support&region=cn-north&language=zh-CN";
  let messageSubmitted = false;
  let feedbackSubmitted = false;

  function sseEvent(sequence: number, type: string, payload: object): string {
    return [
      `id: ${sequence}`,
      `event: ${type}`,
      `data: ${JSON.stringify({
        event_id: `event-e2e-r0-${sequence}`,
        sequence,
        type,
        conversation_id: conversationId,
        request_id: "request-e2e-r0",
        payload,
      })}`,
      "",
      "",
    ].join("\n");
  }

  await page.route(
    `**/v1/conversations/${conversationId}/events**`,
    async (route) => {
      const afterSequence = Number(
        new URL(route.request().url()).searchParams.get("after_sequence") ?? "0",
      );
      if (afterSequence < 1) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: sseEvent(1, "conversation.created", { status: "active" }),
        });
        return;
      }
      if (messageSubmitted && afterSequence < 4) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: [
            sseEvent(2, "message.created", {
              message_id: "message-e2e-r0-customer",
              actor: "customer",
              text: "公开安装说明",
            }),
            sseEvent(3, "workflow.updated", {
              message_id: "message-e2e-r0-customer",
              workflow_status: "answer_generation_started",
              business_action_status: "not_started",
            }),
            sseEvent(4, "answer.completed", {
              message_id: "message-e2e-r0-answer",
              text: "根据「公开安装 FAQ」：请按应用内引导完成首次设置。",
              answer_mode: "r0_self_service",
              citations: [
                {
                  chunk_id: "knowledge-chunk-public-e2e",
                  document_key: "public-setup-faq",
                  document_version: "1.0.0",
                  title: "公开安装 FAQ",
                  excerpt: "请按应用内引导完成首次设置。",
                  product: "demo-support",
                  region: "cn-north",
                  language: "zh-CN",
                  effective_at: "2026-01-01T00:00:00Z",
                  expires_at: null,
                  source_locator: sourceLocator,
                },
              ],
              allowed_actions: [
                { type: "handoff.request", label: "转人工客服" },
                { type: "feedback.helpful", label: "有帮助" },
                { type: "feedback.unhelpful", label: "没有帮助" },
              ],
            }),
          ].join(""),
        });
        return;
      }
      if (feedbackSubmitted && afterSequence < 5) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: sseEvent(5, "feedback.recorded", {
            answer_message_id: "message-e2e-r0-answer",
            feedback: "helpful",
          }),
        });
        return;
      }
      await route.fulfill({ contentType: "text/event-stream", body: "" });
    },
  );
  await page.route(
    `**/v1/conversations/${conversationId}/messages`,
    async (route) => {
      messageSubmitted = true;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: conversationId,
          message_id: "message-e2e-r0-customer",
          accepted_sequence: 3,
          request_id: "request-e2e-r0",
        }),
      });
    },
  );
  await page.route(`**${sourceLocator}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        chunk_id: "knowledge-chunk-public-e2e",
        document_key: "public-setup-faq",
        document_version: "1.0.0",
        title: "公开安装 FAQ",
        text: "请按应用内引导完成首次设置。",
        product: "demo-support",
        region: "cn-north",
        language: "zh-CN",
        effective_at: "2026-01-01T00:00:00Z",
        expires_at: null,
      }),
    });
  });
  await page.route(
    `**/v1/conversations/${conversationId}/feedback`,
    async (route) => {
      feedbackSubmitted = true;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "feedback-e2e-r0",
          conversation_id: conversationId,
          answer_message_id: "message-e2e-r0-answer",
          feedback: "helpful",
          already_recorded: false,
        }),
      });
    },
  );

  await openAs(page, CUSTOMER_ID, `/support/conversations/${conversationId}`);
  await page.getByRole("textbox", { name: "客服消息" }).fill("公开安装说明");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(
    page.getByText("根据「公开安装 FAQ」：请按应用内引导完成首次设置。", {
      exact: true,
    }),
  ).toBeVisible();

  await page.getByText("来源（1）", { exact: true }).click();
  const sourceEvidence = page
    .getByRole("article")
    .filter({ hasText: "公开安装 FAQ" });
  await sourceEvidence.getByRole("button", { name: "查看已授权原文片段" }).click();
  await expect(sourceEvidence.locator("pre")).toHaveText(
    "请按应用内引导完成首次设置。",
  );
  await page.getByRole("button", { name: "有帮助", exact: true }).click();
  await expect(page.getByText("已记录：有帮助", { exact: true })).toBeVisible();
  await expect(page.getByText("感谢你的反馈。", { exact: true })).toBeVisible();
});

test("坐席可见服务端返回的内部证据状态标签", async ({ page }) => {
  const subject = `E2E 知识证据状态 ${Date.now()}`;

  await createTicket(page, subject);
  await page.route("**/v1/knowledge/answer", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        answer: "目前无法根据可验证资料确认，请人工处理。",
        citations: [],
        needs_handoff: true,
        uncertainties: ["conflicting_document_versions"],
      }),
    });
  });

  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);
  await page.getByRole("button", { name: "基于来源回答" }).click();
  await expect(page.getByTestId("knowledge-internal-evidence-status")).toContainText(
    "同一文档存在适用版本冲突",
  );
});

test("客户可创建在线会话、接收受控事件回复并转人工", async ({ page }) => {
  await openAs(page, CUSTOMER_ID, "/support");
  await expect(page.getByRole("textbox", { name: "知识问题" })).toHaveCount(0);
  await page.getByRole("button", { name: "开始在线会话" }).click();
  await expect(page).toHaveURL(/\/support\/conversations\/conversation-/);
  await expect(page.getByText("会话已连接")).toBeVisible();

  await page.getByRole("textbox", { name: "客服消息" }).fill("产品怎么使用？");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(
    page.getByText(
      "已收到你的消息。当前低风险自助 FAQ 尚未向此账户开放；你可以随时请求人工客服协助。",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByText("正在生成，尚非最终答复。")).toHaveCount(0);

  await page.getByRole("button", { name: "转人工客服" }).click();
  await expect(page.getByText("已提交人工客服请求。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("conversation-status")).toContainText("已转人工客服");
});

test("主管可把会话坏例记录为 QA 闭环并验证回归", async ({ page }) => {
  await openAs(page, CUSTOMER_ID, "/support");
  await page.getByRole("button", { name: "开始在线会话" }).click();
  await expect(page).toHaveURL(/\/support\/conversations\/conversation-/);
  const conversationId = page.url().split("/").at(-1);
  expect(conversationId).toBeTruthy();
  await page.getByRole("textbox", { name: "客服消息" }).fill("产品怎么使用？");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(
    page.getByText(
      "已收到你的消息。当前低风险自助 FAQ 尚未向此账户开放；你可以随时请求人工客服协助。",
      { exact: true },
    ),
  ).toBeVisible();

  await openAs(page, SUPERVISOR_ID, "/queue");
  const qaPanel = page.getByLabel("主管 QA 反馈闭环");
  await expect(qaPanel).toBeVisible();
  await qaPanel.getByTestId(`qa-sample-${conversationId}`).click();
  await expect(qaPanel.getByLabel("QA 会话样本详情")).toContainText("最终结果");
  await qaPanel
    .getByRole("combobox", { name: "QA 回归用例" })
    .selectOption("qa-triage-r2-replacement-routing");
  await qaPanel
    .getByRole("combobox", { name: "QA 根因" })
    .selectOption("routing_rule");
  await qaPanel
    .getByRole("textbox", { name: "QA 问题概述" })
    .fill("补发和换货请求应进入受控人工审核。");
  await qaPanel
    .getByRole("textbox", { name: "QA 修复方式" })
    .fill("补发换货规则已加入三层 R2 门禁和回归评测。");
  await qaPanel.getByRole("button", { name: "记录 QA 问题" }).click();
  await expect(qaPanel.getByRole("status")).toContainText("QA 问题已记录");

  await qaPanel
    .getByRole("combobox", { name: "QA 处置状态" })
    .selectOption("in_remediation");
  await qaPanel.getByRole("button", { name: "更新闭环" }).click();
  await expect(qaPanel.getByRole("status")).toContainText("QA 闭环状态已更新");
  await qaPanel
    .getByRole("combobox", { name: "QA 处置状态" })
    .selectOption("verification_pending");
  await qaPanel.getByRole("button", { name: "更新闭环" }).click();
  await expect(qaPanel.getByRole("status")).toContainText("QA 闭环状态已更新");
  await qaPanel
    .getByRole("combobox", { name: "QA 处置状态" })
    .selectOption("verified");
  await qaPanel
    .getByRole("combobox", { name: "QA 回归状态" })
    .selectOption("passed");
  await qaPanel
    .getByRole("textbox", { name: "回归运行证据引用" })
    .fill("playwright:qa-replacement-routing");
  await qaPanel.getByRole("button", { name: "更新闭环" }).click();
  await expect(qaPanel.getByRole("status")).toContainText("QA 闭环状态已更新");
  await expect(qaPanel.getByLabel("QA 闭环详情")).toContainText("已验证");
});

test("客户人工升级会进入主管队列，坐席可凭最小上下文包领取并关闭", async ({ page }) => {
  await openAs(page, CUSTOMER_ID, "/support");
  await page.getByRole("button", { name: "开始在线会话" }).click();
  await expect(page).toHaveURL(/\/support\/conversations\/conversation-/);
  const conversationId = page.url().split("/").at(-1);
  expect(conversationId).toBeTruthy();

  await page.getByRole("button", { name: "转人工客服" }).click();
  await expect(page.getByText("已提交人工客服请求。", { exact: true })).toBeVisible();

  await openAs(page, SUPERVISOR_ID, "/queue");
  const handoffItem = page.getByTestId(`handoff-${conversationId}`);
  await expect(handoffItem).toBeVisible();
  await handoffItem.click();
  await expect(page.getByRole("heading", { name: "升级上下文包" })).toBeVisible();
  await expect(
    page.getByText("此包不包含系统提示词、模型隐式推理、其他客户资料或完整未脱敏轨迹。"),
  ).toBeVisible();

  await openAs(page, AGENT_ID, "/workspace");
  await page.getByTestId(`handoff-${conversationId}`).click();
  await page.getByRole("button", { name: "领取人工升级" }).click();
  await expect(page.getByRole("status")).toContainText("人工升级已领取");
  await page.getByRole("button", { name: "关闭自助会话" }).click();
  await expect(page.getByRole("status")).toContainText("自助会话已关闭");
});

test("已验证客户可通过传统页面查询自己的订单和保修", async ({ page }) => {
  await openAs(page, CUSTOMER_ID, "/support");
  await page.getByRole("link", { name: "订单与保修查询", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "订单与保修查询", exact: true }),
  ).toBeVisible();

  const orderNumber = page.getByRole("textbox", { name: "订单号" });
  await orderNumber.fill("NW-DEMO-ORDER-1001");
  await page.getByRole("button", { name: "查询订单状态" }).click();
  await expect(page.getByLabel("订单状态结果")).toContainText("订单状态：已发货");
  await expect(page.getByLabel("订单状态结果")).toContainText(
    "NW-DEMO-ORDER-1001",
  );

  await page.getByRole("button", { name: "查询保修资格" }).click();
  await expect(page.getByLabel("保修资格结果")).toContainText(
    "当前订单没有匹配的有效保修。",
  );

  await orderNumber.fill("NW-DEMO-ORDER-1002");
  await page.getByRole("button", { name: "查询订单状态" }).click();
  await expect(
    page.getByText(
      "未找到属于当前已验证账户的订单。请核对订单号，或转人工客服。",
      { exact: true },
    ),
  ).toBeVisible();
});

test("R2 售后草稿需要客户确认和坐席审批后才进入受控队列", async ({ page }) => {
  const subject = "Demo shipment has not arrived";
  const ticketId = "ticket-nw-open-01";

  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);
  const agentPanel = page.getByLabel("R2 售后草稿与审批");
  await agentPanel
    .getByRole("combobox", { name: "售后草稿动作" })
    .selectOption("complaint");
  await agentPanel.getByRole("button", { name: "创建售后草稿" }).click();
  await expect(agentPanel.getByRole("status")).toContainText(
    "已创建投诉案例草稿，等待客户确认。",
  );
  await expect(agentPanel).toContainText("等待客户确认");

  await openAs(page, CUSTOMER_ID, `/tickets/${ticketId}`);
  const customerPanel = page.getByLabel("售后草稿确认");
  await expect(customerPanel).toContainText("当前 Mock 未计算金额或退款权益。");
  await customerPanel.getByRole("button", { name: "确认售后草稿" }).click();
  await expect(customerPanel.getByRole("status")).toContainText(
    "草稿已确认，正在等待坐席审批。",
  );
  await expect(customerPanel).toContainText("等待坐席审批");

  await openAs(page, AGENT_ID, "/workspace");
  await selectAgentTicket(page, subject);
  const approvalPanel = page.getByLabel("R2 售后草稿与审批");
  await approvalPanel
    .getByRole("button", { name: "审批并进入受控队列" })
    .click();
  await expect(approvalPanel.getByRole("status")).toContainText(
    "坐席审批已记录；工作流已进入受控执行队列。",
  );
  await expect(approvalPanel).toContainText("已进入受控执行队列");
  await expect(approvalPanel).toContainText("尚未产生外部动作");
});

test("会话生成失败时显示可操作降级提示而非永久等待", async ({ page }) => {
  const conversationId = "conversation-e2e-error";
  let messageSubmitted = false;

  function sseEvent(sequence: number, type: string, payload: object): string {
    return [
      `id: ${sequence}`,
      `event: ${type}`,
      `data: ${JSON.stringify({
        event_id: `event-e2e-${sequence}`,
        sequence,
        type,
        conversation_id: conversationId,
        request_id: "request-e2e-error",
        payload,
      })}`,
      "",
      "",
    ].join("\n");
  }

  await page.route(
    `**/v1/conversations/${conversationId}/events**`,
    async (route) => {
      const afterSequence = Number(
        new URL(route.request().url()).searchParams.get("after_sequence") ?? "0",
      );
      if (afterSequence < 1) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: sseEvent(1, "conversation.created", { status: "active" }),
        });
        return;
      }
      if (messageSubmitted && afterSequence < 4) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: [
            sseEvent(2, "message.created", {
              message_id: "message-e2e-error",
              actor: "customer",
              text: "模拟模型故障",
            }),
            sseEvent(3, "workflow.updated", {
              message_id: "message-e2e-error",
              workflow_status: "answer_generation_started",
              business_action_status: "not_started",
            }),
            sseEvent(4, "error", {
              message_id: "message-e2e-error",
              code: "answer_generation_failed",
              message: "系统暂时无法完成回复。你可以重试或转人工客服。",
              retryable: true,
              allowed_actions: [
                { type: "handoff.request", label: "转人工客服" },
              ],
            }),
          ].join(""),
        });
        return;
      }
      await route.fulfill({ contentType: "text/event-stream", body: "" });
    },
  );
  await page.route(
    `**/v1/conversations/${conversationId}/messages`,
    async (route) => {
      messageSubmitted = true;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: conversationId,
          message_id: "message-e2e-error",
          accepted_sequence: 3,
          request_id: "request-e2e-error",
        }),
      });
    },
  );
  await page.route(
    `**/v1/conversations/${conversationId}/handoffs`,
    async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "handoff-e2e-error",
          conversation_id: conversationId,
          reason_code: "customer_requested",
          status: "requested",
          already_requested: false,
          created_at: "2026-08-24T00:00:00Z",
        }),
      });
    },
  );

  await openAs(page, CUSTOMER_ID, `/support/conversations/${conversationId}`);
  await page.getByRole("textbox", { name: "客服消息" }).fill("模拟模型故障");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(
    page.getByText("系统暂时无法完成回复。你可以重试或转人工客服。", {
      exact: true,
    }),
  ).toContainText("系统暂时无法完成回复。你可以重试或转人工客服。");
  await expect(page.getByRole("button", { name: "转人工客服" })).toBeEnabled();
  await page.getByRole("button", { name: "转人工客服" }).click();
  await expect(page.getByText("人工客服请求已提交。", { exact: true })).toBeVisible();
});
