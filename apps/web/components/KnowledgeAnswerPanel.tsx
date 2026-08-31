"use client";

import { useEffect, useState } from "react";
import { api, getDemoUserId } from "@/lib/api";
import { findDemoUser } from "@/lib/demo-users";
import { isSafeKnowledgeSourceLocator } from "@/lib/security";

type KnowledgeCitation = {
  chunk_id: string;
  document_key: string;
  document_version: string;
  title: string;
  excerpt: string;
  product: string;
  region: string;
  language: string;
  effective_at: string;
  expires_at: string | null;
  source_locator: string;
};

type KnowledgeAnswer = {
  answer: string;
  citations: KnowledgeCitation[];
  needs_handoff: boolean;
  uncertainties: string[];
};

type KnowledgeCitationSource = {
  chunk_id: string;
  document_key: string;
  document_version: string;
  title: string;
  text: string;
  product: string;
  region: string;
  language: string;
  effective_at: string;
  expires_at: string | null;
};

type SourceState = {
  loading: boolean;
  source?: KnowledgeCitationSource;
  error?: string;
};

const UNCERTAINTY_LABELS: Record<string, string> = {
  no_trusted_lexical_evidence: "没有可验证的词面证据",
  conflicting_document_versions: "同一文档存在适用版本冲突",
  missing_citations: "模型未提供可验证引用",
  duplicate_citations: "模型重复引用了同一片段",
  invalid_citation_id: "模型引用不属于本次检索结果",
  model_requested_handoff: "模型返回了需人工确认的结果",
  model_reported_uncertainty: "模型报告证据不足或存在不确定性",
  prompt_injection: "检测到明确的指令覆盖尝试，已转人工处理",
};

function formatTime(value: string | null): string {
  if (!value) return "未设置截止时间";
  return value.replace("T", " ").replace("Z", " UTC");
}

export default function KnowledgeAnswerPanel({
  initialQuestion,
  staffView = false,
}: {
  initialQuestion?: string;
  staffView?: boolean;
}) {
  const [question, setQuestion] = useState("");
  const [region, setRegion] = useState("");
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [sources, setSources] = useState<Record<string, SourceState>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const user = findDemoUser(getDemoUserId());
    setRegion(user?.defaultKnowledgeRegion ?? "cn-north");
  }, []);

  useEffect(() => {
    if (initialQuestion) setQuestion(initialQuestion);
  }, [initialQuestion]);

  async function submit() {
    const query = question.trim();
    if (!query || !region) return;

    setLoading(true);
    setError("");
    setAnswer(null);
    setSources({});
    try {
      const result = await api<KnowledgeAnswer>("/v1/knowledge/answer", {
        method: "POST",
        body: JSON.stringify({
          query,
          product: "demo-support",
          region,
          language: "zh-CN",
        }),
      });
      setAnswer(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "知识回答加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function openSource(citation: KnowledgeCitation) {
    if (!isSafeKnowledgeSourceLocator(citation.source_locator)) {
      setSources((current) => ({
        ...current,
        [citation.chunk_id]: {
          loading: false,
          error: "来源地址不符合受信任的 API 路径。",
        },
      }));
      return;
    }
    setSources((current) => ({
      ...current,
      [citation.chunk_id]: { loading: true },
    }));
    try {
      const source = await api<KnowledgeCitationSource>(citation.source_locator);
      setSources((current) => ({
        ...current,
        [citation.chunk_id]: { loading: false, source },
      }));
    } catch {
      // The source endpoint deliberately returns the same non-sensitive result
      // for missing, expired, cross-tenant and unauthorized documents.
      setSources((current) => ({
        ...current,
        [citation.chunk_id]: {
          loading: false,
          error: "该原文片段当前不可访问。",
        },
      }));
    }
  }

  return (
    <section className="border rounded p-3 space-y-3 bg-slate-50" aria-label="知识回答">
      <div>
        <h2 className="font-semibold">知识库问答</h2>
        <p className="text-sm text-gray-600">
          仅在当前身份可访问且可验证的资料足够时给出带来源的回答。
        </p>
      </div>

      <textarea
        className="border rounded p-2 w-full"
        aria-label="知识问题"
        placeholder="例如：退款流程是什么？"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />

      {staffView ? (
        <label className="block text-sm">
          检索适用地区
          <input
            className="border rounded p-2 ml-2"
            aria-label="知识适用地区"
            value={region}
            onChange={(event) => setRegion(event.target.value)}
          />
        </label>
      ) : (
        <p className="text-xs text-gray-500">
          当前地区由已验证的客户身份在服务端校验。
        </p>
      )}

      <button
        className="bg-violet-700 text-white px-3 py-2 rounded disabled:opacity-50"
        onClick={submit}
        disabled={loading || !question.trim() || !region}
      >
        {loading ? "检索并校验中…" : "基于来源回答"}
      </button>

      {error && (
        <p className="text-red-600 text-sm" role="alert">
          {error}
        </p>
      )}

      {answer && (
        <div className="space-y-3" aria-live="polite">
          <p className="whitespace-pre-wrap">{answer.answer}</p>
          {answer.needs_handoff && (
            <p className="text-amber-800 text-sm">
              该问题需要补充信息或由人工客服确认；系统未提供未经证据支持的结论。
            </p>
          )}

          {staffView && answer.uncertainties.length > 0 && (
            <div
              className="text-xs text-amber-900 bg-amber-100 rounded p-2"
              data-testid="knowledge-internal-evidence-status"
            >
              <span className="font-medium">内部证据状态：</span>
              {answer.uncertainties
                .map((code) => UNCERTAINTY_LABELS[code] ?? "内部验证未通过")
                .join("；")}
            </div>
          )}

          {answer.citations.length > 0 && (
            <details className="border rounded p-2">
              <summary className="cursor-pointer font-medium">
                来源（{answer.citations.length}）
              </summary>
              <div className="pt-3 space-y-3">
                {answer.citations.map((citation) => {
                  const sourceState = sources[citation.chunk_id];
                  const safeSourceLocator = isSafeKnowledgeSourceLocator(
                    citation.source_locator,
                  );
                  return (
                    <article
                      key={citation.chunk_id}
                      className="border-l-2 border-violet-400 pl-3 text-sm space-y-1"
                    >
                      <div className="font-medium">{citation.title}</div>
                      <div>版本：{citation.document_version}</div>
                      <div>
                        生效：{formatTime(citation.effective_at)}；截止：
                        {formatTime(citation.expires_at)}
                      </div>
                      <div>
                        适用范围：产品 {citation.product} · 地区 {citation.region} ·
                        语言 {citation.language}
                      </div>
                      <p className="text-gray-700 whitespace-pre-wrap">
                        {citation.excerpt}
                      </p>
                      {safeSourceLocator ? (
                        <button
                          className="text-blue-700 underline disabled:opacity-50"
                          onClick={() => openSource(citation)}
                          disabled={sourceState?.loading}
                        >
                          {sourceState?.loading
                            ? "正在校验访问权限…"
                            : "查看已授权原文片段"}
                        </button>
                      ) : (
                        <p className="text-red-600" role="alert">
                          来源地址不符合受信任的 API 路径。
                        </p>
                      )}
                      {sourceState?.error && (
                        <p className="text-red-600" role="alert">
                          {sourceState.error}
                        </p>
                      )}
                      {sourceState?.source && (
                        <pre className="bg-white border rounded p-2 whitespace-pre-wrap font-sans">
                          {sourceState.source.text}
                        </pre>
                      )}
                    </article>
                  );
                })}
              </div>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
