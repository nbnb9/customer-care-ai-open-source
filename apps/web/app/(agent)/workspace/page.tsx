"use client";

import { useCallback, useEffect, useState } from "react";
import AfterSalesAgentPanel from "@/components/after-sales/AfterSalesAgentPanel";
import HandoffQueuePanel from "@/components/handoffs/HandoffQueuePanel";
import KnowledgeAnswerPanel from "@/components/KnowledgeAnswerPanel";
import TriageAssistPanel from "@/components/TriageAssistPanel";
import { api, getDemoUserId } from "@/lib/api";
import {
  assignableAgentsFor,
  demoUserLabel,
  findDemoUser,
} from "@/lib/demo-users";
import {
  NEXT_STATUSES,
  priorityLabel,
  statusLabel,
  type Ticket,
  type TicketEvent,
} from "@/lib/tickets";

const EVENT_LABELS: Record<string, string> = {
  created: "工单已创建",
  status_changed: "状态已更新",
  comment_added: "客户可见回复",
  internal_note: "内部备注",
  assigned: "负责人已变更",
};

export default function WorkspacePage() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [events, setEvents] = useState<TicketEvent[]>([]);
  const [currentUserId, setCurrentUserId] = useState("");
  const [assignmentTarget, setAssignmentTarget] = useState("");
  const [loading, setLoading] = useState(true);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [sendingComment, setSendingComment] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionSuccess, setActionSuccess] = useState("");
  const [reply, setReply] = useState("");
  const [note, setNote] = useState("");
  const [nextStatus, setNextStatus] = useState("");

  const agents = assignableAgentsFor(currentUserId);
  const currentUser = findDemoUser(currentUserId);
  const isAgent = currentUser?.role === "agent";
  const isSupervisor = currentUser?.role === "supervisor";
  const allowedStatuses = selected ? NEXT_STATUSES[selected.status] ?? [] : [];

  const loadTickets = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const loaded = await api<Ticket[]>("/v1/tickets");
      setTickets(
        [...loaded].sort((a, b) => b.created_at.localeCompare(a.created_at)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTicket = useCallback(async (id: string) => {
    setActionError("");
    setEventsLoading(true);
    try {
      const [ticket, loadedEvents] = await Promise.all([
        api<Ticket>(`/v1/tickets/${id}`),
        api<TicketEvent[]>(`/v1/tickets/${id}/events`),
      ]);
      const availableAgents = assignableAgentsFor(getDemoUserId());
      setSelected(ticket);
      setEvents(loadedEvents);
      setAssignmentTarget(ticket.assignee_id ?? availableAgents[0]?.id ?? "");
      setNextStatus("");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "加载工单失败");
    } finally {
      setEventsLoading(false);
    }
  }, []);

  useEffect(() => {
    setCurrentUserId(getDemoUserId());
    loadTickets();
  }, [loadTickets]);

  async function assignTicket(assigneeId: string) {
    if (!selected || !assigneeId) return;
    setActionError("");
    setActionSuccess("");
    setAssigning(true);
    try {
      await api<Ticket>(`/v1/tickets/${selected.id}/assignments`, {
        method: "POST",
        body: JSON.stringify({ assignee_id: assigneeId }),
      });
      await Promise.all([loadTicket(selected.id), loadTickets()]);
      setActionSuccess(
        assigneeId === currentUserId && isAgent
          ? "工单已领取"
          : `工单已分配给 ${demoUserLabel(assigneeId)}`,
      );
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "分配失败");
    } finally {
      setAssigning(false);
    }
  }

  async function updateStatus() {
    if (!selected || !nextStatus) return;
    setActionError("");
    setActionSuccess("");
    setUpdatingStatus(true);
    try {
      const updated = await api<Ticket>(`/v1/tickets/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: nextStatus }),
      });
      await Promise.all([loadTicket(updated.id), loadTickets()]);
      setActionSuccess(`状态已更新为“${statusLabel(updated.status)}”`);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "状态更新失败");
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function addComment(
    isInternal: boolean,
    body: string,
    clear: () => void,
  ) {
    if (!selected || !body.trim()) return;
    setActionError("");
    setActionSuccess("");
    setSendingComment(true);
    try {
      await api(`/v1/tickets/${selected.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: body.trim(), is_internal: isInternal }),
      });
      clear();
      await loadTicket(selected.id);
      setActionSuccess(isInternal ? "内部备注已保存" : "客户可见回复已发送");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSendingComment(false);
    }
  }

  function selectTicket(id: string) {
    setActionSuccess("");
    setReply("");
    setNote("");
    loadTicket(id);
  }

  return (
    <div className="cc-page">
      <header className="cc-page-header">
        <div>
          <p className="cc-page-kicker">Priority inbox</p>
          <h1 className="cc-page-title">坐席协同工作台</h1>
          <p className="cc-page-description">
            在同一任务上下文中完成领取、判断、证据回复和受控售后动作。
          </p>
        </div>
        <span className="cc-status-chip">人工主导 · 外部动作受控</span>
      </header>

      {error && (
        <p className="cc-alert cc-alert--danger" role="alert">
          {error}
        </p>
      )}

      <div className="cc-workspace-grid">
        <section className="cc-panel cc-ticket-queue" aria-label="工单优先队列">
          <div className="cc-panel-header">
            <h2>优先队列</h2>
            <span>{tickets.length} 项</span>
          </div>
          {loading ? (
            <p className="cc-empty-state">正在加载授权队列…</p>
          ) : tickets.length === 0 ? (
            <p className="cc-empty-state">当前队列为空。</p>
          ) : (
            <ul className="cc-ticket-list">
              {tickets.map((ticket) => (
                <li key={ticket.id}>
                  <button
                    className="cc-ticket-item"
                    aria-pressed={selected?.id === ticket.id}
                    onClick={() => selectTicket(ticket.id)}
                  >
                    <span className="cc-ticket-item-top">
                      <strong>{ticket.subject}</strong>
                      <span>{priorityLabel(ticket.priority)}</span>
                    </span>
                    <span className="cc-ticket-item-meta">
                      {statusLabel(ticket.status)} · {demoUserLabel(ticket.assignee_id)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="cc-panel cc-work-item" aria-label="当前工单工作区">
          {selected ? (
            <div>
              <div className="cc-work-item-heading">
                <div>
                  <p className="cc-page-kicker">当前任务</p>
                  <h2>{selected.subject}</h2>
                </div>
                <span className="cc-status-chip">{statusLabel(selected.status)}</span>
              </div>

              <div className="cc-work-item-body">
                <section className="cc-customer-message" aria-label="客户问题">
                  <span>客户问题</span>
                  <p>{selected.description || "客户未补充详细描述。"}</p>
                </section>

                <TriageAssistPanel ticketId={selected.id} />

                <KnowledgeAnswerPanel
                  initialQuestion={selected.subject}
                  staffView
                />

                {actionSuccess && (
                  <p className="cc-alert cc-alert--success" role="status">
                    {actionSuccess}
                  </p>
                )}
                {actionError && (
                  <p className="cc-alert cc-alert--danger" role="alert">
                    {actionError}
                  </p>
                )}

                <section className="cc-composer" aria-label="客户可见回复">
                  <div className="cc-composer-heading">
                    <h3>客户可见回复</h3>
                    <span>发送前请核对来源与业务状态</span>
                  </div>
                  <textarea
                    className="cc-field"
                    placeholder="客户可见回复"
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                  />
                  <button
                    className="cc-button cc-button--primary"
                    disabled={!reply.trim() || sendingComment}
                    onClick={() => addComment(false, reply, () => setReply(""))}
                  >
                    发送客户可见回复
                  </button>
                </section>

                <section className="cc-composer cc-composer--internal" aria-label="内部备注">
                  <div className="cc-composer-heading">
                    <h3>内部备注</h3>
                    <span>客户不可见</span>
                  </div>
                  <textarea
                    className="cc-field"
                    placeholder="内部备注（客户不可见）"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <button
                    className="cc-button cc-button--secondary"
                    disabled={!note.trim() || sendingComment}
                    onClick={() => addComment(true, note, () => setNote(""))}
                  >
                    添加内部备注
                  </button>
                </section>

                <section className="cc-timeline" aria-label="工单时间线">
                  <div className="cc-composer-heading">
                    <h3>处理时间线</h3>
                    <span>{events.length} 条事件</span>
                  </div>
                  {eventsLoading ? (
                    <p className="cc-empty-state">正在加载事件…</p>
                  ) : events.length === 0 ? (
                    <p className="cc-empty-state">暂无事件。</p>
                  ) : (
                    <ol>
                      {events.map((event) => (
                        <li
                          key={event.id}
                          className={
                            event.event_type === "internal_note"
                              ? "cc-timeline-item cc-timeline-item--internal"
                              : "cc-timeline-item"
                          }
                        >
                          <span>{EVENT_LABELS[event.event_type] ?? event.event_type}</span>
                          {event.payload_digest && <p>{event.payload_digest}</p>}
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
              </div>
            </div>
          ) : (
            <div className="cc-empty-state cc-empty-state--large">
              <strong>选择一张工单开始处理</strong>
              <p>工单、客户上下文和允许动作会在授权后显示。</p>
            </div>
          )}
        </section>

        <aside className="cc-context-column" aria-label="客户与动作上下文">
          {selected ? (
            <>
              <section className="cc-panel">
                <div className="cc-panel-header">
                  <h2>任务上下文</h2>
                  <span className="cc-status-chip">已授权</span>
                </div>
                <div className="cc-panel-body cc-context-facts">
                  <div>
                    <span>状态</span>
                    <strong>{statusLabel(selected.status)}</strong>
                  </div>
                  <div>
                    <span>优先级</span>
                    <strong>{priorityLabel(selected.priority)}</strong>
                  </div>
                  <div>
                    <span>负责人</span>
                    <strong>{demoUserLabel(selected.assignee_id)}</strong>
                  </div>
                </div>
              </section>

              <section className="cc-panel">
                <div className="cc-panel-header">
                  <h2>负责人和状态</h2>
                </div>
                <div className="cc-panel-body cc-context-actions">
                  {isAgent &&
                    selected.status === "triaged" &&
                    !selected.assignee_id && (
                      <button
                        className="cc-button cc-button--primary"
                        disabled={assigning}
                        onClick={() => assignTicket(currentUserId)}
                      >
                        {assigning ? "领取中…" : "领取工单"}
                      </button>
                    )}
                  {isAgent && selected.status === "new" && (
                    <p className="cc-help-text">
                      先将工单更新为“已分诊”，再领取工单。
                    </p>
                  )}
                  {isAgent && selected.assignee_id && (
                    <p className="cc-help-text">
                      当前负责人：{demoUserLabel(selected.assignee_id)}；重新分配需由主管操作。
                    </p>
                  )}
                  {isSupervisor && (
                    <div className="cc-inline-actions">
                    <select
                      className="cc-field"
                      aria-label="分配坐席"
                      value={assignmentTarget}
                      onChange={(e) => setAssignmentTarget(e.target.value)}
                    >
                      <option value="">选择负责人…</option>
                      {agents.map((agent) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.label}
                        </option>
                      ))}
                    </select>
                    <button
                      className="cc-button cc-button--secondary"
                      disabled={
                        assigning ||
                        !assignmentTarget ||
                        assignmentTarget === selected.assignee_id ||
                        ![
                          "triaged",
                          "assigned",
                          "in_progress",
                          "waiting_customer",
                          "reopened",
                        ].includes(selected.status)
                      }
                      onClick={() => assignTicket(assignmentTarget)}
                    >
                      {selected.assignee_id ? "重新分配" : "分配"}
                    </button>
                    </div>
                  )}
                  <select
                    className="cc-field"
                    aria-label="工单状态"
                    value={nextStatus}
                    onChange={(e) => setNextStatus(e.target.value)}
                  >
                    <option value="">
                      {allowedStatuses.length ? "选择新状态…" : "无可用状态流转"}
                    </option>
                    {allowedStatuses.map((status) => (
                      <option key={status} value={status}>
                        {statusLabel(status)}
                      </option>
                    ))}
                  </select>
                  <button
                    className="cc-button cc-button--primary"
                    onClick={updateStatus}
                    disabled={!nextStatus || updatingStatus}
                  >
                    {updatingStatus ? "更新中…" : "更新状态"}
                  </button>
                </div>
              </section>

              <AfterSalesAgentPanel ticketId={selected.id} />
            </>
          ) : (
            <section className="cc-panel cc-panel-body cc-help-text">
              选择工单后显示客户、订单、政策与允许动作。
            </section>
          )}
          <HandoffQueuePanel mode="agent" />
        </aside>
      </div>
    </div>
  );
}
