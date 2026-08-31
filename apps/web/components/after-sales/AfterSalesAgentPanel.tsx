"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  AFTER_SALES_ACTION_LABELS,
  afterSalesExecutionMessage,
  afterSalesStatusLabel,
  type AfterSalesActionType,
  type AfterSalesWorkflow,
} from "@/lib/after-sales";

type PendingAction = "create" | `approval:${string}:${string}`;

export default function AfterSalesAgentPanel({ ticketId }: { ticketId: string }) {
  const [workflows, setWorkflows] = useState<AfterSalesWorkflow[]>([]);
  const [draftAction, setDraftAction] =
    useState<AfterSalesActionType>("complaint");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const idempotencyKeys = useRef(new Map<string, string>());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setWorkflows(
        await api<AfterSalesWorkflow[]>(
          `/v1/after-sales/workflows?ticket_id=${encodeURIComponent(ticketId)}`,
        ),
      );
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "售后工作流加载失败");
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  useEffect(() => {
    void load();
  }, [load]);

  function keyFor(operation: string): string {
    const existing = idempotencyKeys.current.get(operation);
    if (existing) return existing;
    const key = crypto.randomUUID();
    idempotencyKeys.current.set(operation, key);
    return key;
  }

  function finish(operation: string): void {
    idempotencyKeys.current.delete(operation);
  }

  async function createDraft(): Promise<void> {
    const operation = `create:${ticketId}:${draftAction}`;
    setPending("create");
    setError("");
    setSuccess("");
    try {
      const workflow = await api<AfterSalesWorkflow>("/v1/after-sales/workflows", {
        method: "POST",
        headers: { "Idempotency-Key": keyFor(operation) },
        body: JSON.stringify({ ticket_id: ticketId, action_type: draftAction }),
      });
      finish(operation);
      setSuccess(`已创建${workflow.customer_summary.action_label}草稿，等待客户确认。`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建售后草稿失败");
    } finally {
      setPending(null);
    }
  }

  async function decide(
    workflow: AfterSalesWorkflow,
    decision: "approve" | "reject" | "edit",
    editedActionType?: AfterSalesActionType,
  ): Promise<void> {
    const operation = `approval:${workflow.id}:${decision}:${editedActionType ?? ""}` as PendingAction;
    setPending(operation);
    setError("");
    setSuccess("");
    try {
      const updated = await api<AfterSalesWorkflow>(
        `/v1/after-sales/workflows/${workflow.id}/agent-approval`,
        {
          method: "POST",
          headers: { "Idempotency-Key": keyFor(operation) },
          body: JSON.stringify({
            decision,
            ...(editedActionType ? { edited_action_type: editedActionType } : {}),
          }),
        },
      );
      finish(operation);
      setSuccess(
        decision === "approve"
          ? "坐席审批已记录；工作流已进入受控执行队列。"
          : decision === "reject"
            ? "草稿已拒绝并取消；没有外部动作。"
            : "草稿已编辑，客户需要重新确认。",
      );
      setWorkflows((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新售后草稿失败");
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="border rounded-lg p-4 space-y-4" aria-label="R2 售后草稿与审批">
      <div className="space-y-1">
        <p className="text-sm font-medium text-blue-700">步骤 36 · R2 受控售后</p>
        <h3 className="font-semibold">售后草稿、确认与审批</h3>
        <p className="text-sm text-gray-600">
          规则先校验，客户确认与坐席审批同时满足后才会入队。此界面不能退款、取消订单或向外部发送消息。
        </p>
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        <label className="text-sm" htmlFor={`after-sales-action-${ticketId}`}>
          草稿动作
        </label>
        <select
          id={`after-sales-action-${ticketId}`}
          aria-label="售后草稿动作"
          className="border rounded p-2"
          value={draftAction}
          disabled={pending !== null}
          onChange={(event) => setDraftAction(event.target.value as AfterSalesActionType)}
        >
          {Object.entries(AFTER_SALES_ACTION_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button
          className="bg-indigo-700 text-white px-3 py-2 rounded disabled:opacity-50"
          disabled={pending !== null}
          onClick={() => void createDraft()}
        >
          {pending === "create" ? "创建中…" : "创建售后草稿"}
        </button>
      </div>

      {success && <p className="text-green-700 text-sm" role="status">{success}</p>}
      {error && <p className="text-red-600 text-sm" role="alert">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-500">加载售后工作流…</p>
      ) : workflows.length === 0 ? (
        <p className="text-sm text-gray-500">当前工单没有售后草稿。</p>
      ) : (
        <ul className="space-y-3" aria-label="售后工作流列表">
          {workflows.map((workflow) => {
            const executionMessage = afterSalesExecutionMessage(workflow);
            const editOperation = `approval:${workflow.id}:edit:${draftAction}`;
            return (
              <li
                key={workflow.id}
                className="border rounded p-3 space-y-2"
                data-testid={`after-sales-workflow-${workflow.id}`}
              >
                <div className="flex flex-wrap justify-between gap-2">
                  <strong>{workflow.customer_summary.action_label}</strong>
                  <span className="text-sm text-gray-600">
                    {afterSalesStatusLabel(workflow.status)}
                  </span>
                </div>
                <p className="text-sm text-gray-600">
                  订单：{workflow.order_number} · 规则：{workflow.policy.decision_code}
                </p>
                {executionMessage && (
                  <p className="text-sm text-slate-700">{executionMessage}</p>
                )}
                {workflow.status === "awaiting_agent_approval" && (
                  <div className="flex flex-wrap gap-2 items-center">
                    <button
                      className="bg-green-700 text-white px-3 py-2 rounded disabled:opacity-50"
                      disabled={pending !== null}
                      onClick={() => void decide(workflow, "approve")}
                    >
                      {pending === `approval:${workflow.id}:approve:`
                        ? "审批中…"
                        : "审批并进入受控队列"}
                    </button>
                    <button
                      className="bg-gray-700 text-white px-3 py-2 rounded disabled:opacity-50"
                      disabled={pending !== null}
                      onClick={() => void decide(workflow, "reject")}
                    >
                      拒绝草稿
                    </button>
                    <select
                      className="border rounded p-2"
                      aria-label="编辑后的售后动作"
                      value={draftAction}
                      disabled={pending !== null}
                      onChange={(event) =>
                        setDraftAction(event.target.value as AfterSalesActionType)
                      }
                    >
                      {Object.entries(AFTER_SALES_ACTION_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <button
                      className="border border-indigo-700 text-indigo-800 px-3 py-2 rounded disabled:opacity-50"
                      disabled={pending !== null || draftAction === workflow.action_type}
                      onClick={() => void decide(workflow, "edit", draftAction)}
                    >
                      {pending === editOperation ? "编辑中…" : "编辑并请求重新确认"}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
