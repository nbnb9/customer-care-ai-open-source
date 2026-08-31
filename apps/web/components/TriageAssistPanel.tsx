"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { priorityLabel } from "@/lib/tickets";

type Suggestion = {
  id: string;
  ticket_id: string;
  intent: string;
  risk_level: string;
  route: string;
  summary: string;
  suggested_priority: string;
  missing_information: string[];
  needs_human_review: boolean;
  reason_codes: string[];
  model: string;
  prompt_version: string;
  decision: string | null;
  decision_reason_code: string | null;
  edited_summary: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
};

const REJECT_REASONS = [
  { value: "incorrect_category", label: "分类不正确" },
  { value: "missing_context", label: "上下文不足" },
  { value: "policy_exception", label: "政策例外" },
];

const DECISION_REASON_LABELS: Record<string, string> = {
  ...Object.fromEntries(
    REJECT_REASONS.map((reason) => [reason.value, reason.label]),
  ),
  summary_correction: "摘要修正",
};

const DECISION_LABELS: Record<string, string> = {
  accepted: "坐席已确认",
  edited: "坐席已编辑",
  rejected: "坐席已拒绝",
};

export default function TriageAssistPanel({ ticketId }: { ticketId: string }) {
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [draftSummary, setDraftSummary] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const latest = await api<Suggestion>(`/v1/tickets/${ticketId}/triage`);
      setSuggestion(latest);
      setDraftSummary(latest.edited_summary ?? latest.summary);
    } catch (e) {
      setSuggestion(null);
      if (!(e instanceof ApiError && e.status === 404)) {
        setError(e instanceof Error ? e.message : "建议加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  useEffect(() => {
    setSuggestion(null);
    setEditing(false);
    setRejectReason("");
    setDraftSummary("");
    load();
  }, [load]);

  async function generate() {
    setGenerating(true);
    setError("");
    try {
      const generated = await api<Suggestion>(`/v1/tickets/${ticketId}/triage`, {
        method: "POST",
      });
      setSuggestion(generated);
      setDraftSummary(generated.summary);
      setEditing(false);
      setRejectReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenerating(false);
    }
  }

  async function decide(decision: "accepted" | "edited" | "rejected") {
    if (!suggestion) {
      setError("当前没有可处理的分诊建议");
      return;
    }
    const editedSummary = draftSummary.trim();
    if (decision === "edited" && !editedSummary) {
      setError("编辑后的摘要不能为空");
      return;
    }
    if (decision === "rejected" && !rejectReason) {
      setError("请选择拒绝原因");
      return;
    }

    const body =
      decision === "edited"
        ? {
            suggestion_id: suggestion.id,
            decision,
            reason_code: "summary_correction",
            edited_summary: editedSummary,
          }
        : decision === "rejected"
          ? { suggestion_id: suggestion.id, decision, reason_code: rejectReason }
          : { suggestion_id: suggestion.id, decision };

    setError("");
    setSaving(true);
    try {
      const saved = await api<Suggestion>(
        `/v1/tickets/${ticketId}/triage/decision`,
        { method: "POST", body: JSON.stringify(body) },
      );
      setSuggestion(saved);
      setDraftSummary(saved.edited_summary ?? saved.summary);
      setEditing(false);
      setRejectReason("");
    } catch (e) {
      // 刻意不清空编辑态和 draftSummary，网络恢复后可原样重试。
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  function startEdit() {
    setDraftSummary(suggestion?.edited_summary ?? suggestion?.summary ?? "");
    setEditing(true);
    setError("");
  }

  const decisionLabel = suggestion
    ? suggestion.decision
      ? DECISION_LABELS[suggestion.decision] ?? suggestion.decision
      : "AI 建议 · 尚未处理"
    : null;
  const decisionReasonLabel = suggestion?.decision_reason_code
    ? DECISION_REASON_LABELS[suggestion.decision_reason_code]
    : undefined;

  return (
    <div className="border rounded p-3 space-y-3 bg-gray-50">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-semibold">分诊建议</h3>
        {decisionLabel && (
          <span
            className={`text-xs px-2 py-1 rounded ${
              suggestion?.decision === "rejected"
                ? "bg-red-100 text-red-700"
                : suggestion?.decision
                  ? "bg-green-100 text-green-700"
                  : "bg-blue-100 text-blue-700"
            }`}
          >
            {decisionLabel}
          </span>
        )}
      </div>

      {loading ? (
        <p>加载中…</p>
      ) : suggestion ? (
        <>
          <div className="text-sm space-y-1">
            <div>意图：{suggestion.intent}</div>
            <div>
              建议优先级：{priorityLabel(suggestion.suggested_priority)} · 风险：
              {suggestion.risk_level} · 路由：{suggestion.route}
            </div>
            {editing ? (
              <textarea
                className="border rounded p-2 w-full"
                aria-label="编辑建议摘要"
                value={draftSummary}
                onChange={(e) => setDraftSummary(e.target.value)}
              />
            ) : (
              <>
                <div>AI 原始摘要：{suggestion.summary}</div>
                {suggestion.decision === "edited" && suggestion.edited_summary && (
                  <div className="font-medium text-blue-800">
                    坐席编辑结果：{suggestion.edited_summary}
                  </div>
                )}
              </>
            )}
            {suggestion.missing_information.length > 0 && (
              <div>缺失：{suggestion.missing_information.join("、")}</div>
            )}
            {suggestion.reason_codes.length > 0 && (
              <div className="text-amber-700">
                业务提示：{suggestion.reason_codes.join("、")}
              </div>
            )}
            <div className="text-gray-400 text-xs">
              {suggestion.model} · prompt {suggestion.prompt_version}
            </div>
          </div>

          {!suggestion.decision ? (
            <div className="flex flex-wrap gap-2 items-center">
              <button
                className="bg-green-600 text-white px-3 py-1 rounded disabled:opacity-50"
                disabled={saving}
                onClick={() => decide("accepted")}
              >
                接受建议
              </button>
              {editing ? (
                <button
                  className="bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50"
                  disabled={saving || !draftSummary.trim()}
                  onClick={() => decide("edited")}
                >
                  {saving ? "保存中…" : "保存编辑"}
                </button>
              ) : (
                <button
                  className="bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50"
                  disabled={saving}
                  onClick={startEdit}
                >
                  编辑
                </button>
              )}
              <select
                className="border rounded p-1"
                aria-label="拒绝原因"
                value={rejectReason}
                disabled={saving}
                onChange={(e) => setRejectReason(e.target.value)}
              >
                <option value="">拒绝原因…</option>
                {REJECT_REASONS.map((reason) => (
                  <option key={reason.value} value={reason.value}>
                    {reason.label}
                  </option>
                ))}
              </select>
              <button
                className="bg-red-600 text-white px-3 py-1 rounded disabled:opacity-50"
                disabled={!rejectReason || saving}
                onClick={() => decide("rejected")}
              >
                拒绝建议
              </button>
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              {decisionLabel}
              {suggestion.decision_reason_code
                ? `，原因：${decisionReasonLabel ?? suggestion.decision_reason_code}`
                : ""}
              {suggestion.decided_by ? `，操作人：${suggestion.decided_by}` : ""}
            </p>
          )}
        </>
      ) : (
        <p className="text-sm text-gray-500">暂无建议</p>
      )}

      {!suggestion?.decision && (
        <button
          className="bg-gray-700 text-white px-3 py-1 rounded disabled:opacity-50"
          onClick={generate}
          disabled={generating || saving}
        >
          {generating ? "生成中…" : suggestion ? "重新生成建议" : "生成建议"}
        </button>
      )}
      {error && (
        <p className="text-red-600 text-sm" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
