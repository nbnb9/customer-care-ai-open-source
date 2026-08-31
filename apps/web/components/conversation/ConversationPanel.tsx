"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  API_BASE,
  ApiError,
  api,
  withDemoIdentityHeaders,
} from "@/lib/api";
import { isSafeKnowledgeSourceLocator } from "@/lib/security";

type ConversationEvent = {
  event_id: string;
  sequence: number;
  type: string;
  conversation_id: string;
  request_id: string;
  payload: Record<string, unknown>;
};

type ConversationActor = "customer" | "assistant" | "system";

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

type Feedback = "helpful" | "unhelpful";

type ConversationFeedbackRead = {
  id: string;
  conversation_id: string;
  answer_message_id: string;
  feedback: Feedback;
  already_recorded: boolean;
};

type TimelineMessage = {
  id: string;
  actor: ConversationActor;
  text: string;
  completed: boolean;
  failed?: boolean;
  answerMode?: string;
  citations?: KnowledgeCitation[];
  feedback?: Feedback;
};

type MessageAccepted = {
  conversation_id: string;
  message_id: string;
  accepted_sequence: number;
  request_id: string;
};

type HandoffRead = {
  id: string;
  conversation_id: string;
  reason_code: string;
  status: string;
  already_requested: boolean;
};

const STATUS_LABELS: Record<string, string> = {
  active: "会话进行中",
  awaiting_customer: "等待人工客服后续指引",
  awaiting_agent: "等待人工客服",
  handed_off: "已转人工客服",
  blocked_for_safety: "已转交安全人工处理",
  closed: "会话已关闭",
};

const CONNECTION_LABELS: Record<string, string> = {
  connecting: "正在连接会话事件流…",
  connected: "会话已连接",
  reconnecting: "连接中断，正在按已确认事件恢复…",
  stopped: "会话事件流已停止",
};

const REPLY_TIMEOUT_MS = 30_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function isFeedback(value: unknown): value is Feedback {
  return value === "helpful" || value === "unhelpful";
}

function parseCitation(value: unknown): KnowledgeCitation | null {
  if (!isRecord(value)) return null;
  const expiresAt = value.expires_at;
  const sourceLocator = asString(value.source_locator);
  if (
    !isSafeKnowledgeSourceLocator(sourceLocator) ||
    (expiresAt !== null && typeof expiresAt !== "string")
  ) {
    return null;
  }
  const fields = [
    "chunk_id",
    "document_key",
    "document_version",
    "title",
    "excerpt",
    "product",
    "region",
    "language",
    "effective_at",
  ] as const;
  if (fields.some((field) => typeof value[field] !== "string")) return null;
  return {
    chunk_id: value.chunk_id as string,
    document_key: value.document_key as string,
    document_version: value.document_version as string,
    title: value.title as string,
    excerpt: value.excerpt as string,
    product: value.product as string,
    region: value.region as string,
    language: value.language as string,
    effective_at: value.effective_at as string,
    expires_at: expiresAt,
    source_locator: sourceLocator,
  };
}

function parseCitations(value: unknown): KnowledgeCitation[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(parseCitation)
    .filter((citation): citation is KnowledgeCitation => citation !== null);
}

function formatTime(value: string | null): string {
  if (!value) return "未设置截止时间";
  return value.replace("T", " ").replace("Z", " UTC");
}

function parseSseFrame(frame: string): ConversationEvent | null {
  const fields = new Map<string, string[]>();
  for (const line of frame.replaceAll("\r\n", "\n").split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    if (separator < 0) continue;
    const key = line.slice(0, separator);
    const value = line.slice(separator + 1).replace(/^ /, "");
    fields.set(key, [...(fields.get(key) ?? []), value]);
  }
  const rawData = fields.get("data")?.join("\n");
  if (!rawData) return null;
  try {
    const data: unknown = JSON.parse(rawData);
    if (
      !isRecord(data) ||
      typeof data.event_id !== "string" ||
      typeof data.sequence !== "number" ||
      typeof data.type !== "string" ||
      typeof data.conversation_id !== "string" ||
      typeof data.request_id !== "string" ||
      !isRecord(data.payload)
    ) {
      return null;
    }
    return {
      event_id: data.event_id,
      sequence: data.sequence,
      type: data.type,
      conversation_id: data.conversation_id,
      request_id: data.request_id,
      payload: data.payload,
    };
  } catch {
    return null;
  }
}

async function consumeSse(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: ConversationEvent) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.replaceAll("\r\n", "\n").split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseSseFrame(frame);
        if (event) onEvent(event);
      }
    }
    buffer += decoder.decode();
    const finalEvent = parseSseFrame(buffer);
    if (finalEvent) onEvent(finalEvent);
  } finally {
    reader.releaseLock();
  }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export default function ConversationPanel({
  conversationId,
}: {
  conversationId: string;
}) {
  const lastSequenceRef = useRef(0);
  const replyTimerRef = useRef<number | null>(null);
  const handoffKeyRef = useRef<string | null>(null);
  const feedbackKeysRef = useRef<Record<string, { feedback: Feedback; key: string }>>({});
  const [messages, setMessages] = useState<TimelineMessage[]>([]);
  const [sources, setSources] = useState<Record<string, SourceState>>({});
  const [conversationStatus, setConversationStatus] = useState("active");
  const [lastSequence, setLastSequence] = useState(0);
  const [connectionStatus, setConnectionStatus] = useState("connecting");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [handoffing, setHandoffing] = useState(false);
  const [streamError, setStreamError] = useState("");
  const [replyError, setReplyError] = useState("");
  const [handoffStatus, setHandoffStatus] = useState("");
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const [feedbackPendingMessageId, setFeedbackPendingMessageId] = useState("");
  const [lastRequestId, setLastRequestId] = useState("");

  const clearReplyTimer = useCallback(() => {
    if (replyTimerRef.current !== null) {
      window.clearTimeout(replyTimerRef.current);
      replyTimerRef.current = null;
    }
  }, []);

  const finishReply = useCallback(() => {
    clearReplyTimer();
    setAwaitingReply(false);
  }, [clearReplyTimer]);

  const applyEvent = useCallback(
    (event: ConversationEvent) => {
      const expectedSequence = lastSequenceRef.current + 1;
      if (event.sequence <= lastSequenceRef.current) return;
      if (event.sequence !== expectedSequence) {
        setStreamError("事件序列出现缺口，正在从最后确认的位置重新同步。");
        return;
      }
      lastSequenceRef.current = event.sequence;
      setLastSequence(event.sequence);
      setStreamError("");

      if (event.type === "conversation.created") {
        const status = asString(event.payload.status);
        if (status) setConversationStatus(status);
        return;
      }
      if (event.type === "message.created") {
        const messageId = asString(event.payload.message_id);
        const actor = asString(event.payload.actor);
        const text = asString(event.payload.text);
        if (
          !messageId ||
          (actor !== "customer" && actor !== "system") ||
          text === null
        ) {
          return;
        }
        setMessages((current) => {
          const existing = current.find((message) => message.id === messageId);
          if (existing) {
            return current.map((message) =>
              message.id === messageId
                ? { ...message, actor, text, completed: true }
                : message,
            );
          }
          return [...current, { id: messageId, actor, text, completed: true }];
        });
        return;
      }
      if (event.type === "answer.delta") {
        const messageId = asString(event.payload.message_id);
        const delta = asString(event.payload.delta);
        if (!messageId || delta === null) return;
        setMessages((current) => {
          const existing = current.find((message) => message.id === messageId);
          if (!existing) {
            return [
              ...current,
              { id: messageId, actor: "assistant", text: delta, completed: false },
            ];
          }
          return current.map((message) =>
            message.id === messageId
              ? { ...message, text: `${message.text}${delta}` }
              : message,
          );
        });
        return;
      }
      if (event.type === "answer.completed") {
        const messageId = asString(event.payload.message_id);
        const text = asString(event.payload.text);
        if (!messageId || text === null) return;
        const answerMode = asString(event.payload.answer_mode) ?? "controlled_ack";
        const citations =
          answerMode === "r0_self_service"
            ? parseCitations(event.payload.citations)
            : [];
        setMessages((current) => {
          const existing = current.find((message) => message.id === messageId);
          if (!existing) {
            return [
              ...current,
              {
                id: messageId,
                actor: "assistant",
                text,
                completed: true,
                answerMode,
                citations,
              },
            ];
          }
          return current.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  text,
                  completed: true,
                  failed: false,
                  answerMode,
                  citations,
                }
              : message,
          );
        });
        finishReply();
        return;
      }
      if (event.type === "feedback.recorded") {
        const answerMessageId = asString(event.payload.answer_message_id);
        const feedback = event.payload.feedback;
        if (!answerMessageId || !isFeedback(feedback)) return;
        setMessages((current) =>
          current.map((message) =>
            message.id === answerMessageId ? { ...message, feedback } : message,
          ),
        );
        return;
      }
      if (event.type === "handoff.created") {
        const reason = asString(event.payload.reason_code);
        setConversationStatus(
          reason === "safety_review" ? "blocked_for_safety" : "handed_off",
        );
        setHandoffStatus(
          reason === "safety_review"
            ? "已转交安全人工处理。"
            : "已提交人工客服请求。",
        );
        finishReply();
        return;
      }
      if (event.type === "handoff.updated") {
        const handoffState = asString(event.payload.status);
        if (handoffState === "claimed") {
          setHandoffStatus("人工客服已接手。");
        } else if (handoffState === "closed") {
          setHandoffStatus("人工客服已关闭本次自助会话。");
        } else if (handoffState === "safe_flow_restored") {
          setHandoffStatus("已恢复到安全人工跟进流程；不会自动调用模型回复。");
        }
        return;
      }
      if (event.type === "workflow.updated") {
        const status = asString(event.payload.conversation_status);
        if (status) setConversationStatus(status);
        return;
      }
      if (event.type === "error") {
        const message = asString(event.payload.message);
        setLastRequestId(event.request_id);
        setReplyError(message ?? "回答未能完成，请重试或转人工客服。");
        setMessages((current) =>
          current.map((item) =>
            item.actor === "assistant" && !item.completed
              ? { ...item, failed: true }
              : item,
          ),
        );
        finishReply();
      }
    },
    [finishReply],
  );

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let delay = 250;

    async function connect(): Promise<void> {
      while (!cancelled) {
        controller = new AbortController();
        try {
          setConnectionStatus(lastSequenceRef.current ? "reconnecting" : "connecting");
          const response = await fetch(
            `${API_BASE}/v1/conversations/${encodeURIComponent(conversationId)}/events?after_sequence=${lastSequenceRef.current}`,
            {
              cache: "no-store",
              headers: withDemoIdentityHeaders({ Accept: "text/event-stream" }),
              signal: controller.signal,
            },
          );
          if (!response.ok) {
            const body: unknown = await response.json().catch(() => null);
            const message =
              isRecord(body) && typeof body.message === "string"
                ? body.message
                : `会话事件流请求失败（HTTP ${response.status}）。`;
            if ([401, 403, 404].includes(response.status)) {
              setConnectionStatus("stopped");
              setStreamError(message);
              return;
            }
            throw new Error(message);
          }
          if (!response.body) throw new Error("浏览器未提供可读事件流。");
          setConnectionStatus("connected");
          delay = 250;
          await consumeSse(response.body, applyEvent);
        } catch (error) {
          if (cancelled || controller.signal.aborted) return;
          setStreamError(
            error instanceof Error
              ? `${error.message} 正在恢复连接。`
              : "事件流连接失败，正在恢复连接。",
          );
        }
        if (cancelled) return;
        setConnectionStatus("reconnecting");
        await wait(delay);
        delay = Math.min(delay * 2, 2_000);
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      clearReplyTimer();
    };
  }, [applyEvent, clearReplyTimer, conversationId]);

  function startReplyTimeout(): void {
    clearReplyTimer();
    replyTimerRef.current = window.setTimeout(() => {
      setAwaitingReply(false);
      setReplyError("回答未在预期时间内完成。你可以转人工客服，或稍后重新发送消息。");
    }, REPLY_TIMEOUT_MS);
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending || awaitingReply) return;
    setSending(true);
    setReplyError("");
    setHandoffStatus("");
    try {
      const key = crypto.randomUUID();
      const accepted = await api<MessageAccepted>(
        `/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
        {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: JSON.stringify({ text }),
        },
      );
      setMessages((current) =>
        current.some((message) => message.id === accepted.message_id)
          ? current
          : [
              ...current,
              {
                id: accepted.message_id,
                actor: "customer",
                text,
                completed: true,
              },
            ],
      );
      setDraft("");
      setAwaitingReply(true);
      startReplyTimeout();
    } catch (error) {
      if (error instanceof ApiError && error.requestId) {
        setLastRequestId(error.requestId);
      }
      setReplyError(error instanceof Error ? error.message : "消息发送失败。");
    } finally {
      setSending(false);
    }
  }

  async function requestHandoff(): Promise<void> {
    if (handoffing || conversationStatus !== "active") {
      return;
    }
    setHandoffing(true);
    setReplyError("");
    try {
      handoffKeyRef.current ??= crypto.randomUUID();
      const handoff = await api<HandoffRead>(
        `/v1/conversations/${encodeURIComponent(conversationId)}/handoffs`,
        {
          method: "POST",
          headers: { "Idempotency-Key": handoffKeyRef.current },
          body: JSON.stringify({ reason_code: "customer_requested" }),
        },
      );
      setHandoffStatus(
        handoff.already_requested ? "人工客服请求已存在。" : "人工客服请求已提交。",
      );
      finishReply();
      handoffKeyRef.current = null;
    } catch (error) {
      if (error instanceof ApiError && error.requestId) {
        setLastRequestId(error.requestId);
      }
      const message = error instanceof ApiError ? error.message : "转人工请求失败。";
      setReplyError(message);
    } finally {
      setHandoffing(false);
    }
  }

  async function openSource(citation: KnowledgeCitation): Promise<void> {
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
      // The server intentionally uses one non-sensitive result for missing,
      // expired and unauthorized source data.
      setSources((current) => ({
        ...current,
        [citation.chunk_id]: {
          loading: false,
          error: "该原文片段当前不可访问。",
        },
      }));
    }
  }

  async function submitFeedback(
    answerMessageId: string,
    feedback: Feedback,
  ): Promise<void> {
    if (feedbackPendingMessageId) return;
    const previous = feedbackKeysRef.current[answerMessageId];
    const key =
      previous?.feedback === feedback ? previous.key : crypto.randomUUID();
    feedbackKeysRef.current[answerMessageId] = { feedback, key };
    setFeedbackPendingMessageId(answerMessageId);
    setFeedbackStatus("");
    setReplyError("");
    try {
      const recorded = await api<ConversationFeedbackRead>(
        `/v1/conversations/${encodeURIComponent(conversationId)}/feedback`,
        {
          method: "POST",
          headers: { "Idempotency-Key": key },
          body: JSON.stringify({ answer_message_id: answerMessageId, feedback }),
        },
      );
      setMessages((current) =>
        current.map((message) =>
          message.id === recorded.answer_message_id
            ? { ...message, feedback: recorded.feedback }
            : message,
        ),
      );
      setFeedbackStatus(
        recorded.already_recorded ? "该评价已记录。" : "感谢你的反馈。",
      );
      delete feedbackKeysRef.current[answerMessageId];
    } catch (error) {
      if (error instanceof ApiError && error.requestId) {
        setLastRequestId(error.requestId);
      }
      setReplyError(error instanceof Error ? error.message : "反馈提交失败。")
    } finally {
      setFeedbackPendingMessageId("");
    }
  }

  const statusLabel = STATUS_LABELS[conversationStatus] ?? conversationStatus;

  return (
    <main className="p-8 max-w-3xl mx-auto space-y-5">
      <div className="space-y-1">
        <p className="text-sm font-medium text-blue-700">步骤 35 · 人工升级与上下文包</p>
        <h1 className="text-2xl font-bold">在线客服会话</h1>
        <p className="text-sm text-gray-600">
          会话状态：<span data-testid="conversation-status">{statusLabel}</span> ·
          已确认事件 #{lastSequence}
        </p>
        <p className="text-sm text-gray-600" role="status" aria-live="polite">
          {CONNECTION_LABELS[connectionStatus] ?? connectionStatus}
        </p>
      </div>

      <section className="border rounded-lg p-4 space-y-4" aria-label="客户会话消息">
        {messages.length === 0 ? (
          <p className="text-gray-500">请输入问题，或直接请求人工客服。</p>
        ) : (
          <ol className="space-y-3">
            {messages.map((message) => (
              <li
                key={message.id}
                className={`rounded p-3 whitespace-pre-wrap ${
                  message.actor === "customer"
                    ? "bg-blue-50 border border-blue-100"
                    : message.actor === "system"
                      ? "bg-amber-50 border border-amber-200"
                      : "bg-slate-50 border"
                }`}
              >
                <div className="text-xs text-gray-500 mb-1">
                  {message.actor === "customer"
                    ? "你"
                    : message.actor === "system"
                      ? "系统升级通知"
                      : "客服系统"}
                </div>
                {message.failed ? (
                  <p className="text-gray-500">本次回复未完成，不能作为最终答复。</p>
                ) : (
                  <p>{message.text}</p>
                )}
                {message.actor === "assistant" && !message.completed && !message.failed && (
                  <p className="text-xs text-gray-500 mt-2">正在生成，尚非最终答复。</p>
                )}
                {message.actor === "assistant" &&
                  message.completed &&
                  message.answerMode === "r0_self_service" &&
                  (message.citations?.length ?? 0) > 0 && (
                    <details className="border rounded p-2 mt-3 bg-white">
                      <summary className="cursor-pointer font-medium">
                        来源（{message.citations?.length ?? 0}）
                      </summary>
                      <div className="pt-3 space-y-3">
                        {message.citations?.map((citation) => {
                          const sourceState = sources[citation.chunk_id];
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
                              <button
                                className="text-blue-700 underline disabled:opacity-50"
                                type="button"
                                onClick={() => openSource(citation)}
                                disabled={sourceState?.loading}
                              >
                                {sourceState?.loading
                                  ? "正在校验访问权限…"
                                  : "查看已授权原文片段"}
                              </button>
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
                {message.actor === "assistant" &&
                  message.completed &&
                  message.answerMode === "r0_self_service" && (
                    <div className="border-t mt-3 pt-3 space-y-2" aria-label="回答反馈">
                      <p className="text-sm text-gray-700">
                        这条公开 FAQ 回答是否有帮助？
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          className="border border-emerald-700 text-emerald-800 px-3 py-1 rounded disabled:opacity-50"
                          type="button"
                          aria-pressed={message.feedback === "helpful"}
                          disabled={feedbackPendingMessageId === message.id}
                          onClick={() => submitFeedback(message.id, "helpful")}
                        >
                          有帮助
                        </button>
                        <button
                          className="border border-slate-600 text-slate-800 px-3 py-1 rounded disabled:opacity-50"
                          type="button"
                          aria-pressed={message.feedback === "unhelpful"}
                          disabled={feedbackPendingMessageId === message.id}
                          onClick={() => submitFeedback(message.id, "unhelpful")}
                        >
                          没有帮助
                        </button>
                      </div>
                      {message.feedback && (
                        <p className="text-xs text-gray-600">
                          已记录：{message.feedback === "helpful" ? "有帮助" : "没有帮助"}
                        </p>
                      )}
                    </div>
                  )}
              </li>
            ))}
          </ol>
        )}

        {awaitingReply && (
          <p className="text-sm text-gray-600" role="status">
            正在准备受控回复；业务操作尚未开始。
          </p>
        )}
        {streamError && (
          <p className="text-sm text-amber-800" role="status">
            {streamError}
          </p>
        )}
        {replyError && (
          <p className="text-sm text-red-700" role="alert">
            {replyError}
          </p>
        )}
        {(streamError || replyError) && lastRequestId && (
          <p className="text-xs text-gray-500">
            请求编号：{lastRequestId}
          </p>
        )}
        {handoffStatus && (
          <p className="text-sm text-green-700" role="status">
            {handoffStatus}
          </p>
        )}
        {feedbackStatus && (
          <p className="text-sm text-green-700" role="status">
            {feedbackStatus}
          </p>
        )}
      </section>

      <p className="text-xs text-gray-500">
        仅在当前账户获灰度且检索到可验证的公开资料时，系统才会给出带来源的 FAQ
        回答；无证据、退款、账户、隐私、投诉、安全和人工请求会转交人工处理。
      </p>

      <form className="space-y-2" onSubmit={sendMessage}>
        <label className="block font-medium" htmlFor="conversation-message">
          客服消息
        </label>
        <textarea
          id="conversation-message"
          className="border rounded p-2 w-full"
          aria-label="客服消息"
          placeholder="请描述你的问题"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={sending || awaitingReply || conversationStatus !== "active"}
        />
        <div className="flex flex-wrap gap-2">
          <button
            className="bg-blue-600 text-white px-4 py-2 rounded disabled:opacity-50"
            type="submit"
            disabled={
              !draft.trim() ||
              sending ||
              awaitingReply ||
              conversationStatus !== "active"
            }
          >
            {sending ? "发送中…" : awaitingReply ? "等待回复…" : "发送消息"}
          </button>
          <button
            className="border border-amber-700 text-amber-800 px-4 py-2 rounded disabled:opacity-50"
            type="button"
            disabled={handoffing || conversationStatus !== "active"}
            onClick={requestHandoff}
          >
            {handoffing ? "请求中…" : "转人工客服"}
          </button>
        </div>
      </form>
    </main>
  );
}
