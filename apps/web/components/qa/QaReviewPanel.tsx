"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  QA_CATEGORY_LABELS,
  QA_REGRESSION_STATUS_LABELS,
  QA_REMEDIATION_LABELS,
  QA_ROOT_CAUSE_LABELS,
  QA_SEVERITY_LABELS,
  QA_STATUS_LABELS,
  QA_STATUS_TRANSITIONS,
  type QAConversationSample,
  type QAConversationSampleListItem,
  type QAOwner,
  type QARegressionCatalogEntry,
  type QARegressionStatus,
  type QARootCause,
  type QAReviewIssue,
  type QAReviewIssueDetail,
  type QAReviewStatus,
  type QASeverity,
} from "@/lib/qa";

const SEVERITIES: QASeverity[] = ["low", "medium", "high", "critical"];
const ROOT_CAUSES: QARootCause[] = [
  "knowledge_data",
  "retrieval",
  "routing_rule",
  "authorization",
  "tool_contract",
  "ui",
  "workflow",
  "model_behavior",
  "agent_process",
];
const REGRESSION_STATUSES: QARegressionStatus[] = [
  "not_run",
  "passed",
  "failed",
];

function displayTime(value: string): string {
  return value.replace("T", " ").replace("Z", " UTC");
}

export default function QaReviewPanel() {
  const [samples, setSamples] = useState<QAConversationSampleListItem[]>([]);
  const [catalog, setCatalog] = useState<QARegressionCatalogEntry[]>([]);
  const [owners, setOwners] = useState<QAOwner[]>([]);
  const [issues, setIssues] = useState<QAReviewIssue[]>([]);
  const [selectedSample, setSelectedSample] = useState<QAConversationSample | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<QAReviewIssueDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [regressionCaseId, setRegressionCaseId] = useState("");
  const [severity, setSeverity] = useState<QASeverity>("high");
  const [ownerId, setOwnerId] = useState("");
  const [rootCause, setRootCause] = useState<QARootCause>("knowledge_data");
  const [problemSummary, setProblemSummary] = useState("");
  const [remediationSummary, setRemediationSummary] = useState("");
  const [pauseR0, setPauseR0] = useState(false);
  const [nextStatus, setNextStatus] = useState<QAReviewStatus>("open");
  const [nextRegressionStatus, setNextRegressionStatus] =
    useState<QARegressionStatus>("not_run");
  const [evidenceRef, setEvidenceRef] = useState("");
  const idempotencyKeys = useRef(new Map<string, string>());

  const selectedRegression = useMemo(
    () => catalog.find((entry) => entry.id === regressionCaseId) ?? null,
    [catalog, regressionCaseId],
  );
  const transitionTargets = selectedIssue
    ? QA_STATUS_TRANSITIONS[selectedIssue.status]
    : [];

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [loadedSamples, loadedCatalog, loadedOwners, loadedIssues] =
        await Promise.all([
          api<QAConversationSampleListItem[]>("/v1/admin/qa/samples?limit=20"),
          api<QARegressionCatalogEntry[]>("/v1/admin/qa/regression-cases"),
          api<QAOwner[]>("/v1/admin/qa/owners"),
          api<QAReviewIssue[]>("/v1/admin/qa/issues"),
        ]);
      setSamples(loadedSamples);
      setCatalog(loadedCatalog);
      setOwners(loadedOwners);
      setIssues(loadedIssues);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "QA 数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!catalog.some((entry) => entry.id === regressionCaseId)) {
      setRegressionCaseId(catalog[0]?.id ?? "");
    }
  }, [catalog, regressionCaseId]);

  useEffect(() => {
    if (!owners.some((owner) => owner.id === ownerId)) {
      setOwnerId(owners[0]?.id ?? "");
    }
  }, [ownerId, owners]);

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

  function setIssueDetail(issue: QAReviewIssueDetail): void {
    setSelectedIssue(issue);
    setNextStatus(QA_STATUS_TRANSITIONS[issue.status][0] ?? issue.status);
    setNextRegressionStatus(issue.regression.status);
    setEvidenceRef(issue.regression.evidence_ref ?? "");
  }

  async function chooseSample(conversationId: string): Promise<void> {
    setSampleLoading(true);
    setPending(`sample:${conversationId}`);
    setError("");
    setSuccess("");
    try {
      setSelectedSample(
        await api<QAConversationSample>(`/v1/admin/qa/samples/${conversationId}`),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "QA 样本加载失败");
    } finally {
      setSampleLoading(false);
      setPending("");
    }
  }

  async function chooseIssue(issueId: string): Promise<void> {
    setPending(`issue:${issueId}`);
    setError("");
    setSuccess("");
    try {
      setIssueDetail(
        await api<QAReviewIssueDetail>(`/v1/admin/qa/issues/${issueId}`),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "QA 问题加载失败");
    } finally {
      setPending("");
    }
  }

  async function createIssue(): Promise<void> {
    if (!selectedSample || !selectedRegression || !ownerId) {
      setError("请选择会话样本、回归用例和负责人。");
      return;
    }
    if (!problemSummary.trim() || !remediationSummary.trim()) {
      setError("请填写问题概述和修复方式。");
      return;
    }
    const operation = `create:${selectedSample.conversation_id}:${regressionCaseId}:${problemSummary.trim()}:${remediationSummary.trim()}`;
    setPending("create");
    setError("");
    setSuccess("");
    try {
      const created = await api<QAReviewIssueDetail>("/v1/admin/qa/issues", {
        method: "POST",
        headers: { "Idempotency-Key": keyFor(operation) },
        body: JSON.stringify({
          source_conversation_id: selectedSample.conversation_id,
          category: selectedRegression.category,
          severity,
          owner_id: ownerId,
          root_cause: rootCause,
          problem_summary: problemSummary.trim(),
          remediation_type: selectedRegression.remediation_type,
          remediation_summary: remediationSummary.trim(),
          regression_case_id: selectedRegression.id,
          pause_r0_feature_flag: pauseR0,
        }),
      });
      finish(operation);
      setIssueDetail(created);
      setProblemSummary("");
      setRemediationSummary("");
      setPauseR0(false);
      setSuccess("QA 问题已记录，并已绑定可执行回归用例。");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "QA 问题记录失败");
    } finally {
      setPending("");
    }
  }

  async function updateIssue(): Promise<void> {
    if (!selectedIssue || transitionTargets.length === 0) return;
    if (nextStatus === "verified" && !evidenceRef.trim()) {
      setError("标记为已验证前，必须填写回归运行证据引用。");
      return;
    }
    const operation = `update:${selectedIssue.id}:${nextStatus}:${nextRegressionStatus}:${evidenceRef.trim()}`;
    setPending("update");
    setError("");
    setSuccess("");
    try {
      const updated = await api<QAReviewIssueDetail>(
        `/v1/admin/qa/issues/${selectedIssue.id}`,
        {
          method: "PATCH",
          headers: { "Idempotency-Key": keyFor(operation) },
          body: JSON.stringify({
            status: nextStatus,
            regression_status: nextRegressionStatus,
            ...(evidenceRef.trim()
              ? { regression_evidence_ref: evidenceRef.trim() }
              : {}),
          }),
        },
      );
      finish(operation);
      setIssueDetail(updated);
      setIssues((current) =>
        current.map((issue) => (issue.id === updated.id ? updated : issue)),
      );
      setSuccess("QA 闭环状态已更新。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "QA 闭环更新失败");
    } finally {
      setPending("");
    }
  }

  return (
    <section className="border rounded-lg p-4 space-y-5" aria-label="主管 QA 反馈闭环">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="text-sm font-medium text-indigo-700">步骤 37 · 质量闭环</p>
          <h2 className="font-semibold">主管 QA 采样、修复与回归</h2>
          <p className="text-sm text-gray-600">
            样本按租户隔离并在展示前做直接标识符脱敏；问题只保存证据摘要和哈希，不复制原始会话。
          </p>
        </div>
        <button
          className="border rounded px-3 py-2 disabled:opacity-50"
          disabled={loading || pending !== ""}
          onClick={() => void load()}
        >
          刷新 QA 数据
        </button>
      </div>

      {success && <p className="text-sm text-green-700" role="status">{success}</p>}
      {error && <p className="text-sm text-red-600" role="alert">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-500">加载 QA 样本与回归目录…</p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          <div className="space-y-3">
            <h3 className="font-medium">最近可复核会话样本</h3>
            {samples.length === 0 ? (
              <p className="text-sm text-gray-500">当前没有已完成的会话样本。</p>
            ) : (
              <ul className="space-y-2" aria-label="QA 会话样本列表">
                {samples.map((sample) => (
                  <li key={sample.conversation_id} className="border rounded p-3">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-sm break-all">{sample.final_result_preview}</p>
                        <p className="mt-1 text-xs text-gray-500">
                          {displayTime(sample.created_at)} · {sample.answer_mode ?? "未标注模式"} · 引用 {sample.citation_count}
                        </p>
                      </div>
                      <button
                        className="border rounded px-3 py-1.5 text-sm disabled:opacity-50"
                        data-testid={`qa-sample-${sample.conversation_id}`}
                        disabled={pending !== ""}
                        onClick={() => void chooseSample(sample.conversation_id)}
                      >
                        {pending === `sample:${sample.conversation_id}` ? "加载中…" : "查看样本"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {sampleLoading && <p className="text-sm text-gray-500">正在加载样本详情…</p>}
            {selectedSample && (
              <article className="border rounded p-3 space-y-3" aria-label="QA 会话样本详情">
                <div>
                  <h4 className="font-medium">最终结果</h4>
                  <p className="mt-1 text-sm whitespace-pre-wrap">{selectedSample.final_result.text}</p>
                  <p className="mt-1 text-xs text-gray-500">
                    模式：{selectedSample.final_result.answer_mode ?? "未标注"} · 路由：{selectedSample.route.conversation_status}
                  </p>
                </div>
                <div>
                  <h4 className="font-medium">引用与工具摘要</h4>
                  {selectedSample.citations.length === 0 ? (
                    <p className="text-sm text-gray-500">此最终结果没有客户可见引用。</p>
                  ) : (
                    <ul className="mt-1 text-sm list-disc pl-5">
                      {selectedSample.citations.map((citation) => (
                        <li key={`${citation.document_key}:${citation.document_version}`}>
                          {citation.title} · {citation.document_key}@{citation.document_version}
                        </li>
                      ))}
                    </ul>
                  )}
                  {selectedSample.tool_summaries.length > 0 && (
                    <ul className="mt-1 text-sm list-disc pl-5">
                      {selectedSample.tool_summaries.map((tool) => (
                        <li key={`${tool.tool_name}:${tool.result_status}`}>
                          {tool.tool_name}：{tool.result_status}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4 className="font-medium">脱敏会话片段</h4>
                  <ul className="mt-1 space-y-1 text-sm">
                    {selectedSample.messages.map((message) => (
                      <li key={message.id} className="rounded bg-gray-50 p-2">
                        <span className="font-medium">{message.actor}：</span>
                        {message.text}
                      </li>
                    ))}
                  </ul>
                </div>
              </article>
            )}
          </div>

          <form
            className="border rounded p-3 space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              void createIssue();
            }}
          >
            <div>
              <h3 className="font-medium">记录可修复 QA 问题</h3>
              <p className="mt-1 text-sm text-gray-600">
                每个问题必须绑定已纳入公开合成评测集的回归用例；这不是生产 CI 证明。
              </p>
            </div>
            <label className="block text-sm">
              QA 回归用例
              <select
                className="mt-1 w-full border rounded p-2"
                aria-label="QA 回归用例"
                value={regressionCaseId}
                disabled={pending !== "" || catalog.length === 0}
                onChange={(event) => setRegressionCaseId(event.target.value)}
              >
                {catalog.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.id} · {entry.description}
                  </option>
                ))}
              </select>
            </label>
            {selectedRegression && (
              <p className="text-sm text-gray-600">
                类别：{QA_CATEGORY_LABELS[selectedRegression.category]} · 修复：{QA_REMEDIATION_LABELS[selectedRegression.remediation_type]}
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                QA 严重程度
                <select
                  className="mt-1 w-full border rounded p-2"
                  aria-label="QA 严重程度"
                  value={severity}
                  disabled={pending !== ""}
                  onChange={(event) => {
                    const value = event.target.value as QASeverity;
                    setSeverity(value);
                    if (value === "low" || value === "medium") setPauseR0(false);
                  }}
                >
                  {SEVERITIES.map((value) => (
                    <option key={value} value={value}>{QA_SEVERITY_LABELS[value]}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                QA 负责人
                <select
                  className="mt-1 w-full border rounded p-2"
                  aria-label="QA 负责人"
                  value={ownerId}
                  disabled={pending !== "" || owners.length === 0}
                  onChange={(event) => setOwnerId(event.target.value)}
                >
                  {owners.map((owner) => (
                    <option key={owner.id} value={owner.id}>
                      {owner.display_name} · {owner.role}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="block text-sm">
              根因
              <select
                className="mt-1 w-full border rounded p-2"
                aria-label="QA 根因"
                value={rootCause}
                disabled={pending !== ""}
                onChange={(event) => setRootCause(event.target.value as QARootCause)}
              >
                {ROOT_CAUSES.map((value) => (
                  <option key={value} value={value}>{QA_ROOT_CAUSE_LABELS[value]}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              QA 问题概述
              <textarea
                className="mt-1 min-h-20 w-full border rounded p-2"
                aria-label="QA 问题概述"
                maxLength={1000}
                value={problemSummary}
                disabled={pending !== ""}
                onChange={(event) => setProblemSummary(event.target.value)}
              />
            </label>
            <label className="block text-sm">
              QA 修复方式
              <textarea
                className="mt-1 min-h-20 w-full border rounded p-2"
                aria-label="QA 修复方式"
                maxLength={1000}
                value={remediationSummary}
                disabled={pending !== ""}
                onChange={(event) => setRemediationSummary(event.target.value)}
              />
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                aria-label="暂停此租户 R0 自助服务"
                type="checkbox"
                checked={pauseR0}
                disabled={pending !== "" || !["high", "critical"].includes(severity)}
                onChange={(event) => setPauseR0(event.target.checked)}
              />
              <span>
                暂停此租户 R0 自助服务（仅高/严重问题；此操作只能关闭，不能重新开启）。
              </span>
            </label>
            <button
              className="bg-indigo-700 text-white px-3 py-2 rounded disabled:opacity-50"
              type="submit"
              disabled={pending !== "" || !selectedSample || !selectedRegression || !ownerId}
            >
              {pending === "create" ? "记录中…" : "记录 QA 问题"}
            </button>
          </form>
        </div>
      )}

      <div className="space-y-3">
        <h3 className="font-medium">已记录问题与验证状态</h3>
        {issues.length === 0 ? (
          <p className="text-sm text-gray-500">尚未记录 QA 问题。</p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2" aria-label="QA 问题列表">
            {issues.map((issue) => (
              <li key={issue.id} className="border rounded p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-medium">
                      {QA_CATEGORY_LABELS[issue.category]} · {QA_SEVERITY_LABELS[issue.severity]}
                    </p>
                    <p className="mt-1 text-sm text-gray-600">{issue.problem_summary}</p>
                    <p className="mt-1 text-xs text-gray-500">
                      {QA_STATUS_LABELS[issue.status]} · 回归 {QA_REGRESSION_STATUS_LABELS[issue.regression.status]}
                    </p>
                  </div>
                  <button
                    className="border rounded px-3 py-1.5 text-sm disabled:opacity-50"
                    data-testid={`qa-issue-${issue.id}`}
                    disabled={pending !== ""}
                    onClick={() => void chooseIssue(issue.id)}
                  >
                    {pending === `issue:${issue.id}` ? "加载中…" : "查看闭环"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {selectedIssue && (
          <article className="border rounded p-3 space-y-3" aria-label="QA 闭环详情">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h4 className="font-medium">{QA_CATEGORY_LABELS[selectedIssue.category]}闭环</h4>
                <p className="mt-1 text-sm text-gray-600">
                  当前：{QA_STATUS_LABELS[selectedIssue.status]} · {selectedIssue.regression.evaluation_suite}:{selectedIssue.regression.evaluation_case_id}
                </p>
              </div>
              {selectedIssue.feature_flag.paused && (
                <span className="rounded bg-amber-100 px-2 py-1 text-sm text-amber-900">
                  R0 已暂停
                </span>
              )}
            </div>
            <p className="text-sm">修复：{selectedIssue.remediation_summary}</p>
            <p className="text-xs text-gray-500">
              已记录 {selectedIssue.events.length} 个不可变事件；源会话证据以摘要和哈希保存。
            </p>
            {transitionTargets.length === 0 ? (
              <p className="text-sm text-gray-500">该问题已关闭，不能继续变更状态。</p>
            ) : (
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,2fr)_auto] md:items-end">
                <label className="block text-sm">
                  QA 处置状态
                  <select
                    className="mt-1 w-full border rounded p-2"
                    aria-label="QA 处置状态"
                    value={nextStatus}
                    disabled={pending !== ""}
                    onChange={(event) => setNextStatus(event.target.value as QAReviewStatus)}
                  >
                    {transitionTargets.map((status) => (
                      <option key={status} value={status}>{QA_STATUS_LABELS[status]}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm">
                  QA 回归状态
                  <select
                    className="mt-1 w-full border rounded p-2"
                    aria-label="QA 回归状态"
                    value={nextRegressionStatus}
                    disabled={pending !== ""}
                    onChange={(event) => setNextRegressionStatus(event.target.value as QARegressionStatus)}
                  >
                    {REGRESSION_STATUSES.map((status) => (
                      <option key={status} value={status}>{QA_REGRESSION_STATUS_LABELS[status]}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm">
                  回归运行证据引用
                  <input
                    className="mt-1 w-full border rounded p-2"
                    aria-label="回归运行证据引用"
                    maxLength={255}
                    placeholder="例如 pytest:rag-qa-current-return-policy-v2"
                    value={evidenceRef}
                    disabled={pending !== ""}
                    onChange={(event) => setEvidenceRef(event.target.value)}
                  />
                </label>
                <button
                  className="bg-green-700 text-white px-3 py-2 rounded disabled:opacity-50"
                  disabled={pending !== ""}
                  onClick={() => void updateIssue()}
                >
                  {pending === "update" ? "更新中…" : "更新闭环"}
                </button>
              </div>
            )}
          </article>
        )}
      </div>
    </section>
  );
}
