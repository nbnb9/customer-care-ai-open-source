"use client";

import { useCallback, useEffect, useState } from "react";
import { api, getLatestApiTrace } from "@/lib/api";

type Overview = {
  generated_at: string;
  retained_span_count: number;
  analysis_window_span_count: number;
  customer_experience: Record<string, unknown>;
  ai_quality: Record<string, unknown>;
  safety: Record<string, unknown>;
  workflow: Record<string, unknown>;
  cost_capacity: Record<string, unknown>;
  limitations: string[];
};

type TraceSpan = {
  span_id: string;
  name: string;
  status: string;
  duration_ms: number;
  request_id: string | null;
  conversation_id: string | null;
  ticket_id: string | null;
  workflow_id: string | null;
  attributes: Record<string, unknown>;
};

type Trace = {
  trace_id: string;
  request_ids: string[];
  conversation_ids: string[];
  ticket_ids: string[];
  workflow_ids: string[];
  spans: TraceSpan[];
};

const DASHBOARDS: Array<{ key: keyof Omit<Overview, "generated_at" | "retained_span_count" | "analysis_window_span_count" | "limitations">; label: string }> = [
  { key: "customer_experience", label: "客户体验" },
  { key: "ai_quality", label: "AI 质量" },
  { key: "safety", label: "安全" },
  { key: "workflow", label: "Workflow" },
  { key: "cost_capacity", label: "成本/容量" },
];

function displayValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}

export default function ObservabilityPanel() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [conversationId, setConversationId] = useState("");
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [traceLoading, setTraceLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastCorrelation, setLastCorrelation] = useState("");

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setOverview(await api<Overview>("/v1/admin/observability/overview"));
      const trace = getLatestApiTrace();
      setLastCorrelation(trace?.traceId ?? "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "可观测性概览加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  async function loadConversationTrace() {
    const value = conversationId.trim();
    if (!value) return;
    setTraceLoading(true);
    setError("");
    try {
      setTraces(
        await api<Trace[]>(
          `/v1/admin/observability/traces/conversations/${encodeURIComponent(value)}`,
        ),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "会话 Trace 加载失败");
    } finally {
      setTraceLoading(false);
    }
  }

  return (
    <section className="border rounded-lg p-4 space-y-4" aria-label="本地可观测性">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-indigo-700">步骤 39 · 本地 Trace Viewer</p>
          <h2 className="font-semibold">可观测性、版本与成本摘要</h2>
          <p className="text-sm text-gray-600">
            仅显示脱敏关联元数据；不展示客户正文、提示词、模型输出、密钥或 SQL 参数。
          </p>
        </div>
        <button
          className="border rounded px-3 py-2 disabled:opacity-50"
          disabled={loading}
          onClick={() => void loadOverview()}
        >
          刷新概览
        </button>
      </div>

      {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
      {lastCorrelation && (
        <p className="text-xs text-gray-500 break-all">
          最近 API Trace：{lastCorrelation}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">加载本地 Trace 摘要…</p>
      ) : overview ? (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            {DASHBOARDS.map(({ key, label }) => (
              <article key={key} className="border rounded p-3 space-y-1">
                <h3 className="font-medium text-sm">{label}</h3>
                {Object.entries(overview[key]).map(([metric, value]) => (
                  <p key={metric} className="text-xs break-words">
                    <span className="text-gray-500">{metric}：</span>
                    {displayValue(value)}
                  </p>
                ))}
              </article>
            ))}
          </div>
          <p className="text-xs text-gray-500">
            当前本地保留 {overview.retained_span_count} 条 span；概览基于最近 {overview.analysis_window_span_count} 条。生成于 {overview.generated_at.replace("T", " ").replace("Z", " UTC")}。
          </p>
          <ul className="text-xs text-amber-800 bg-amber-50 rounded p-3 list-disc pl-5">
            {overview.limitations.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </>
      ) : null}

      <form
        className="border rounded p-3 space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          void loadConversationTrace();
        }}
      >
        <h3 className="font-medium">按会话复核 Trace</h3>
        <p className="text-xs text-gray-500">
          输入当前租户的会话 ID，可追踪请求、检索/模型摘要与受控 Workflow 状态。
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            className="border rounded p-2 min-w-64"
            aria-label="会话 Trace ID"
            value={conversationId}
            onChange={(event) => setConversationId(event.target.value)}
            placeholder="conversation-…"
          />
          <button
            className="border rounded px-3 py-2 disabled:opacity-50"
            disabled={traceLoading || !conversationId.trim()}
          >
            {traceLoading ? "查询中…" : "查看 Trace"}
          </button>
        </div>
        {traces.length === 0 && !traceLoading ? (
          <p className="text-xs text-gray-500">尚未查询到本会话的本地 Trace。</p>
        ) : (
          <div className="space-y-3" data-testid="observability-trace-results">
            {traces.map((trace) => (
              <article key={trace.trace_id} className="border rounded p-3 space-y-2">
                <p className="text-xs break-all">Trace：{trace.trace_id}</p>
                <p className="text-xs break-all">请求：{trace.request_ids.join("、") || "—"}</p>
                <ul className="space-y-1 text-xs">
                  {trace.spans.map((span) => (
                    <li key={span.span_id} className="rounded bg-gray-50 p-2">
                      {span.name} · {span.status} · {span.duration_ms} ms
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        )}
      </form>
    </section>
  );
}
