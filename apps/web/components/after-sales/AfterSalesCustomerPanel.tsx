"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  afterSalesExecutionMessage,
  afterSalesStatusLabel,
  type AfterSalesWorkflow,
} from "@/lib/after-sales";

export default function AfterSalesCustomerPanel({ ticketId }: { ticketId: string }) {
  const [workflows, setWorkflows] = useState<AfterSalesWorkflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [pendingWorkflowId, setPendingWorkflowId] = useState<string | null>(null);
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
      setError(cause instanceof Error ? cause.message : "售后草稿加载失败");
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function confirm(
    workflow: AfterSalesWorkflow,
    decision: "confirm" | "cancel",
  ): Promise<void> {
    const operation = `${workflow.id}:${decision}`;
    const existing = idempotencyKeys.current.get(operation);
    const idempotencyKey = existing ?? crypto.randomUUID();
    idempotencyKeys.current.set(operation, idempotencyKey);
    setPendingWorkflowId(workflow.id);
    setError("");
    setSuccess("");
    try {
      const updated = await api<AfterSalesWorkflow>(
        `/v1/after-sales/workflows/${workflow.id}/customer-confirmation`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: JSON.stringify({ decision }),
        },
      );
      idempotencyKeys.current.delete(operation);
      setWorkflows((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setSuccess(
        decision === "confirm"
          ? "草稿已确认，正在等待坐席审批。"
          : "草稿已取消；没有外部动作。",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "确认售后草稿失败");
    } finally {
      setPendingWorkflowId(null);
    }
  }

  if (loading) return <p className="text-sm text-gray-500">加载售后草稿…</p>;
  if (workflows.length === 0) return null;

  return (
    <section className="border rounded-lg p-4 mb-6 space-y-4" aria-label="售后草稿确认">
      <div className="space-y-1">
        <p className="text-sm font-medium text-blue-700">步骤 36 · R2 受控售后</p>
        <h2 className="font-semibold">请确认售后草稿</h2>
        <p className="text-sm text-gray-600">
          本页只确认已验证账户下的草稿。确认后仍需坐席审批；不会直接退款、取消订单或发送外部消息。
        </p>
      </div>

      {success && <p className="text-green-700 text-sm" role="status">{success}</p>}
      {error && <p className="text-red-600 text-sm" role="alert">{error}</p>}

      <ul className="space-y-3" aria-label="客户售后草稿">
        {workflows.map((workflow) => {
          const executionMessage = afterSalesExecutionMessage(workflow);
          return (
            <li key={workflow.id} className="border rounded p-3 space-y-2">
              <div className="flex flex-wrap justify-between gap-2">
                <strong>{workflow.customer_summary.action_label}</strong>
                <span className="text-sm text-gray-600">
                  {afterSalesStatusLabel(workflow.status)}
                </span>
              </div>
              <p className="text-sm">订单：{workflow.order_number}</p>
              <p className="text-sm">影响：{workflow.customer_summary.impact_summary}</p>
              <p className="text-sm">
                金额/权益：
                {workflow.customer_summary.amount_minor === null
                  ? "当前 Mock 未计算金额或退款权益。"
                  : `${workflow.customer_summary.amount_minor} ${workflow.customer_summary.currency ?? ""}`}
              </p>
              <ul className="list-disc list-inside text-sm text-gray-700">
                {workflow.customer_summary.risk_notices.map((notice) => (
                  <li key={notice}>{notice}</li>
                ))}
              </ul>
              {executionMessage && (
                <p className="text-sm text-slate-700">{executionMessage}</p>
              )}
              {workflow.status === "awaiting_customer_confirmation" && (
                <div className="flex flex-wrap gap-2">
                  <button
                    className="bg-green-700 text-white px-3 py-2 rounded disabled:opacity-50"
                    disabled={pendingWorkflowId !== null}
                    onClick={() => void confirm(workflow, "confirm")}
                  >
                    {pendingWorkflowId === workflow.id ? "提交中…" : "确认售后草稿"}
                  </button>
                  <button
                    className="bg-gray-700 text-white px-3 py-2 rounded disabled:opacity-50"
                    disabled={pendingWorkflowId !== null}
                    onClick={() => void confirm(workflow, "cancel")}
                  >
                    取消草稿
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
