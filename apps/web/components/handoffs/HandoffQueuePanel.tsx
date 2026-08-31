"use client";

import { useCallback, useEffect, useState } from "react";
import { api, getDemoUserId } from "@/lib/api";
import { demoUserLabel } from "@/lib/demo-users";
import {
  formatHandoffDate,
  handoffPriorityLabel,
  handoffReasonLabel,
  handoffStatusLabel,
  type HandoffDetail,
  type HandoffQueueItem,
} from "@/lib/handoffs";

type HandoffQueuePanelProps = {
  mode: "agent" | "supervisor";
};

export default function HandoffQueuePanel({ mode }: HandoffQueuePanelProps) {
  const [handoffs, setHandoffs] = useState<HandoffQueueItem[]>([]);
  const [selected, setSelected] = useState<HandoffDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [acting, setActing] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const loaded = await api<HandoffQueueItem[]>("/v1/handoffs");
      setHandoffs(loaded);
      setSelected((current) =>
        current && loaded.some((item) => item.id === current.id) ? current : null,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "人工升级队列加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function selectHandoff(handoffId: string) {
    setDetailLoading(true);
    setError("");
    setSuccess("");
    try {
      setSelected(await api<HandoffDetail>(`/v1/handoffs/${handoffId}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "人工升级上下文加载失败");
    } finally {
      setDetailLoading(false);
    }
  }

  async function claim() {
    if (!selected || acting) return;
    setActing("claim");
    setError("");
    setSuccess("");
    try {
      const updated = await api<HandoffDetail>(`/v1/handoffs/${selected.id}/claim`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({}),
      });
      setSelected(updated);
      setSuccess("人工升级已领取；上下文包已固定，后续状态改变已记录。");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "领取人工升级失败");
    } finally {
      setActing("");
    }
  }

  async function resolve(action: "close_self_service" | "restore_safe_flow") {
    if (!selected || acting) return;
    setActing(action);
    setError("");
    setSuccess("");
    try {
      const updated = await api<HandoffDetail>(
        `/v1/handoffs/${selected.id}/resolution`,
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ action }),
        },
      );
      setSelected(updated);
      setSuccess(
        action === "close_self_service"
          ? "自助会话已关闭。"
          : "已恢复为不触发模型自动回复的安全人工流程。",
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新人工升级失败");
    } finally {
      setActing("");
    }
  }

  const isSafetySupervisor =
    mode === "supervisor" && selected?.target_queue === "safety-review";
  const canClaim =
    (mode === "agent" || isSafetySupervisor) &&
    selected?.status === "requested" &&
    !selected.assigned_agent_id;
  const canResolve =
    (mode === "agent" || isSafetySupervisor) &&
    selected?.status === "claimed" &&
    selected.assigned_agent_id === getDemoUserId();

  return (
    <section className="border rounded-lg p-4 space-y-4" aria-label="人工升级队列">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">人工升级队列</h2>
          <p className="text-sm text-gray-600">
            {mode === "supervisor"
              ? "查看本租户待处理升级与 SLA，不显示完整会话轨迹。"
              : "领取待处理升级，并依据最小上下文包继续人工流程。"}
          </p>
        </div>
        <button
          className="border rounded px-3 py-2 disabled:opacity-50"
          type="button"
          disabled={loading}
          onClick={() => void load()}
        >
          刷新人工队列
        </button>
      </div>

      {success && (
        <p className="text-sm text-green-700" role="status">
          {success}
        </p>
      )}
      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}

      {loading ? (
        <p>加载中…</p>
      ) : handoffs.length === 0 ? (
        <p className="text-sm text-gray-500">当前没有待处理人工升级。</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ul className="space-y-2" aria-label="人工升级列表">
            {handoffs.map((handoff) => (
              <li key={handoff.id}>
                <button
                  className={`w-full text-left border rounded p-3 disabled:opacity-50 ${
                    selected?.id === handoff.id ? "border-indigo-500 bg-indigo-50" : ""
                  }`}
                  type="button"
                  data-testid={`handoff-${handoff.conversation_id}`}
                  disabled={detailLoading}
                  onClick={() => void selectHandoff(handoff.id)}
                >
                  <div className="flex flex-wrap justify-between gap-2 font-medium">
                    <span>{handoffReasonLabel(handoff.reason_code)}</span>
                    <span>{handoffPriorityLabel(handoff.priority)}优先级</span>
                  </div>
                  <div className="mt-1 text-sm text-gray-600">
                    {handoffStatusLabel(handoff.status)} · 队列：{handoff.target_queue}
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    SLA：{formatHandoffDate(handoff.sla_due_at)} · 负责人：
                    {demoUserLabel(handoff.assigned_agent_id)}
                  </div>
                </button>
              </li>
            ))}
          </ul>

          <div className="border rounded p-3 space-y-4">
            {detailLoading ? (
              <p>正在加载最小上下文包…</p>
            ) : selected ? (
              <>
                <div className="space-y-1">
                  <h3 className="font-semibold">升级上下文包</h3>
                  <p className="text-sm text-gray-600">
                    {handoffReasonLabel(selected.reason_code)} ·
                    {handoffStatusLabel(selected.status)} · SLA：
                    {formatHandoffDate(selected.sla_due_at)}
                  </p>
                  <p className="text-xs text-gray-500">
                    验证等级：{selected.context.verification_level ?? "未提供"} ·
                    负责人：{demoUserLabel(selected.assigned_agent_id)}
                  </p>
                </div>

                <div>
                  <h4 className="font-medium">会话摘要</h4>
                  <p className="text-sm text-gray-700 whitespace-pre-wrap">
                    {selected.context.conversation_summary}
                  </p>
                </div>

                <ContextList
                  title="已确认事实"
                  empty="暂无额外已确认事实。"
                  items={selected.context.confirmed_facts.map(
                    (fact) => `${fact.label}：${fact.value}`,
                  )}
                />
                <ContextList
                  title="已查工具摘要"
                  empty="本会话尚无已关联工具摘要。"
                  items={selected.context.tool_summaries.map(
                    (tool) => `${tool.tool_name}：${tool.result_status}`,
                  )}
                />
                <ContextList
                  title="未解决问题"
                  empty="暂无可安全摘录的问题。"
                  items={selected.context.unresolved_questions}
                />
                <ContextList
                  title="风险原因"
                  empty="暂无。"
                  items={selected.context.risk_reasons.map(handoffReasonLabel)}
                />

                <div>
                  <h4 className="font-medium">客户已见来源</h4>
                  {selected.context.customer_visible_citations.length === 0 ? (
                    <p className="text-sm text-gray-500">此升级前没有可复用的公开引用。</p>
                  ) : (
                    <ul className="mt-1 space-y-2 text-sm">
                      {selected.context.customer_visible_citations.map((citation) => (
                        <li key={citation.source_locator} className="border-l-2 pl-2">
                          <div className="font-medium">
                            {citation.title} · {citation.document_version}
                          </div>
                          <p className="text-gray-700 whitespace-pre-wrap">
                            {citation.excerpt}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <p className="text-xs text-gray-500">
                  此包不包含系统提示词、模型隐式推理、其他客户资料或完整未脱敏轨迹。
                </p>

                {(mode === "agent" || isSafetySupervisor) && (
                  <div className="flex flex-wrap gap-2 border-t pt-3">
                    <button
                      className="bg-indigo-600 text-white px-3 py-2 rounded disabled:opacity-50"
                      type="button"
                      disabled={!canClaim || Boolean(acting)}
                      onClick={() => void claim()}
                    >
                      {acting === "claim"
                        ? "领取中…"
                        : isSafetySupervisor
                          ? "领取安全升级"
                          : "领取人工升级"}
                    </button>
                    <button
                      className="bg-slate-700 text-white px-3 py-2 rounded disabled:opacity-50"
                      type="button"
                      disabled={!canResolve || Boolean(acting)}
                      onClick={() => void resolve("close_self_service")}
                    >
                      {acting === "close_self_service" ? "关闭中…" : "关闭自助会话"}
                    </button>
                    <button
                      className="border border-emerald-700 text-emerald-800 px-3 py-2 rounded disabled:opacity-50"
                      type="button"
                      disabled={!canResolve || Boolean(acting)}
                      onClick={() => void resolve("restore_safe_flow")}
                    >
                      {acting === "restore_safe_flow" ? "恢复中…" : "恢复安全流程"}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-gray-500">选择一条人工升级查看最小上下文包。</p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function ContextList({
  title,
  empty,
  items,
}: {
  title: string;
  empty: string;
  items: string[];
}) {
  return (
    <div>
      <h4 className="font-medium">{title}</h4>
      {items.length === 0 ? (
        <p className="text-sm text-gray-500">{empty}</p>
      ) : (
        <ul className="mt-1 list-disc pl-5 text-sm text-gray-700 space-y-1">
          {items.map((item, index) => (
            <li key={`${title}-${index}`} className="whitespace-pre-wrap">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
