"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { statusLabel, type Ticket } from "@/lib/tickets";

export default function SupportPage() {
  const router = useRouter();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [creating, setCreating] = useState(false);
  const [startingConversation, setStartingConversation] = useState(false);
  const submissionRef = useRef<{ fingerprint: string; key: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTickets(await api<Ticket[]>("/v1/tickets"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function create() {
    const payload = {
      subject: subject.trim(),
      description: description.trim() || null,
    };
    if (!payload.subject) {
      setError("请填写工单主题");
      return;
    }

    setError("");
    setSuccess("");
    setCreating(true);
    try {
      const fingerprint = JSON.stringify(payload);
      if (submissionRef.current?.fingerprint !== fingerprint) {
        submissionRef.current = { fingerprint, key: crypto.randomUUID() };
      }
      await api("/v1/tickets", {
        method: "POST",
        headers: { "Idempotency-Key": submissionRef.current.key },
        body: JSON.stringify(payload),
      });
      submissionRef.current = null;
      setSubject("");
      setDescription("");
      setSuccess("工单已创建");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function startConversation() {
    setError("");
    setSuccess("");
    setStartingConversation(true);
    try {
      const conversation = await api<{ id: string }>("/v1/conversations", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({}),
      });
      router.push(`/support/conversations/${conversation.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建在线会话失败");
    } finally {
      setStartingConversation(false);
    }
  }

  return (
    <div className="cc-page cc-page--narrow cc-support-page">
      <section className="cc-support-hero">
        <div>
          <p className="cc-page-kicker">Aster Audio Care</p>
          <h1>您好，需要我们帮您处理什么？</h1>
          <p>
            查询订单和保修、继续在线会话，或提交需要人工跟进的服务请求。每一步都会明确当前状态和下一位责任人。
          </p>
        </div>
        <span className="cc-support-availability">人工客服在线</span>
      </section>

      {success && <p className="cc-alert cc-alert--success" role="status">{success}</p>}
      {error && <p className="cc-alert cc-alert--danger" role="alert">{error}</p>}

      <div className="cc-support-actions">
        <section className="cc-panel cc-support-action" aria-label="在线客服会话">
          <span className="cc-support-icon" aria-hidden="true">会</span>
          <div>
            <h2>在线客服会话</h2>
            <p>发送问题、查看服务进度，或随时请求人工客服。</p>
          </div>
          <button
            className="cc-button cc-button--primary"
            onClick={startConversation}
            disabled={startingConversation}
          >
            {startingConversation ? "正在创建会话…" : "开始在线会话"}
          </button>
        </section>

        <section className="cc-panel cc-support-action" aria-label="订单与保修查询">
          <span className="cc-support-icon cc-support-icon--teal" aria-hidden="true">单</span>
          <div>
            <h2>订单与保修</h2>
            <p>只读查询当前账户的订单和保修状态，不执行修改。</p>
          </div>
          <Link className="cc-button cc-button--secondary" href="/support/order-lookup">
            订单与保修查询
          </Link>
        </section>
      </div>

      <section className="cc-panel cc-request-form" aria-labelledby="new-request-title">
        <div className="cc-panel-header">
          <div>
            <h2 id="new-request-title">提交人工服务请求</h2>
            <p className="cc-help-text">我们会保留处理记录，并在状态变化时更新。</p>
          </div>
        </div>
        <div className="cc-panel-body">
          <div className="cc-form-stack">
            <label className="cc-field-label">
              <span>问题主题</span>
              <input
                className="cc-field"
                placeholder="主题"
                value={subject}
                onChange={(e) => {
                  setSubject(e.target.value);
                  setSuccess("");
                }}
                required
              />
            </label>
            <label className="cc-field-label">
              <span>补充描述</span>
              <textarea
                className="cc-field"
                placeholder="描述"
                value={description}
                onChange={(e) => {
                  setDescription(e.target.value);
                  setSuccess("");
                }}
              />
            </label>
            <button
              className="cc-button cc-button--primary"
              onClick={create}
              disabled={!subject.trim() || creating}
            >
              {creating ? "创建中…" : "创建工单"}
            </button>
          </div>
        </div>
      </section>

      <section className="cc-recent-cases" aria-labelledby="recent-cases-title">
        <div className="cc-section-heading">
          <div>
            <h2 id="recent-cases-title">最近服务记录</h2>
            <p>查看人工请求的当前状态和公开回复。</p>
          </div>
          <span>{tickets.length} 条</span>
        </div>

        {loading ? (
          <p className="cc-empty-state">正在加载服务记录…</p>
        ) : tickets.length === 0 ? (
          <p className="cc-empty-state">暂无服务记录。</p>
        ) : (
          <ul className="cc-case-list">
            {tickets.map((ticket) => (
              <li key={ticket.id}>
                <Link
                  href={`/tickets/${ticket.id}`}
                  aria-label={ticket.subject}
                >
                  <span>
                    <strong>{ticket.subject}</strong>
                    <small>查看处理进度与客服回复</small>
                  </span>
                  <span className="cc-status-chip">{statusLabel(ticket.status)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
