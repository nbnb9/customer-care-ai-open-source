"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import HandoffQueuePanel from "@/components/handoffs/HandoffQueuePanel";
import ObservabilityPanel from "@/components/observability/ObservabilityPanel";
import QaReviewPanel from "@/components/qa/QaReviewPanel";
import { api, getDemoUserId } from "@/lib/api";
import {
  assignableAgentsFor,
  demoUserLabel,
  findDemoUser,
} from "@/lib/demo-users";
import {
  priorityLabel,
  statusLabel,
  type Ticket,
} from "@/lib/tickets";

export default function QueuePage() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [currentUserId, setCurrentUserId] = useState("");
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [assigningId, setAssigningId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const loadSequenceRef = useRef(0);

  const agents = assignableAgentsFor(currentUserId);
  const isSupervisor = findDemoUser(currentUserId)?.role === "supervisor";

  const load = useCallback(async () => {
    const sequence = ++loadSequenceRef.current;
    setLoading(true);
    setError("");
    try {
      const loaded = await api<Ticket[]>("/v1/tickets");
      if (sequence !== loadSequenceRef.current) return;
      const sorted = [...loaded].sort((a, b) =>
        b.created_at.localeCompare(a.created_at),
      );
      const availableAgents = assignableAgentsFor(getDemoUserId());
      setTickets(sorted);
      setTargets(
        Object.fromEntries(
          sorted.map((ticket) => [
            ticket.id,
            ticket.assignee_id ?? availableAgents[0]?.id ?? "",
          ]),
        ),
      );
    } catch (e) {
      if (sequence !== loadSequenceRef.current) return;
      setError(e instanceof Error ? e.message : "队列加载失败");
    } finally {
      if (sequence === loadSequenceRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setCurrentUserId(getDemoUserId());
    load();
  }, [load]);

  async function assign(ticket: Ticket) {
    const assigneeId = targets[ticket.id];
    if (!assigneeId) return;
    setAssigningId(ticket.id);
    setError("");
    setSuccess("");
    try {
      const updated = await api<Ticket>(
        `/v1/tickets/${ticket.id}/assignments`,
        {
          method: "POST",
          body: JSON.stringify({ assignee_id: assigneeId }),
        },
      );
      setTickets((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setTargets((current) => ({
        ...current,
        [updated.id]: updated.assignee_id ?? "",
      }));
      setSuccess(`“${updated.subject}”已分配给 ${demoUserLabel(updated.assignee_id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "重新分配失败");
    } finally {
      setAssigningId("");
    }
  }

  const unassignedCount = tickets.filter((ticket) => !ticket.assignee_id).length;
  const activeCount = tickets.filter(
    (ticket) => !["closed", "resolved"].includes(ticket.status),
  ).length;

  return (
    <div className="cc-page cc-ops-page">
      <header className="cc-page-header">
        <div>
          <p className="cc-page-kicker">After-sales operations</p>
          <h1 className="cc-page-title">售后运营概览</h1>
          <p className="cc-page-description">
            先处理队列与安全风险，再从异常下钻到质量和脱敏技术证据。
          </p>
        </div>
        <button
          className="cc-button cc-button--secondary"
          disabled={loading}
          onClick={load}
        >
          刷新队列
        </button>
      </header>

      <div className="cc-metric-grid">
        <div className="cc-panel cc-metric-card">
          <span>全部工单</span>
          <strong>{tickets.length}</strong>
          <small>当前授权租户</small>
        </div>
        <div className="cc-panel cc-metric-card">
          <span>处理中</span>
          <strong>{activeCount}</strong>
          <small>未解决或关闭</small>
        </div>
        <div className="cc-panel cc-metric-card cc-metric-card--warning">
          <span>待分配</span>
          <strong>{unassignedCount}</strong>
          <small>需要运营处理</small>
        </div>
      </div>

      {success && (
        <p className="cc-alert cc-alert--success" role="status">
          {success}
        </p>
      )}
      {error && (
        <p className="cc-alert cc-alert--danger" role="alert">
          {error}
        </p>
      )}

      <section className="cc-panel cc-queue-table" aria-labelledby="queue-table-title">
        <div className="cc-panel-header">
          <div>
            <h2 id="queue-table-title">队列与负责人</h2>
            <p className="cc-help-text">重新分配只影响当前租户，并继续保留审计事件。</p>
          </div>
          <span className="cc-status-chip">{unassignedCount} 项待分配</span>
        </div>
        {loading ? (
          <p className="cc-empty-state">正在加载运营队列…</p>
        ) : tickets.length === 0 ? (
          <p className="cc-empty-state">当前队列没有工单。</p>
        ) : (
          <div className="cc-table-scroll">
          <table>
            <thead>
              <tr>
                <th>主题</th>
                <th>状态</th>
                <th>优先级</th>
                <th>当前负责人</th>
                <th>重新分配</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((ticket) => (
                <tr key={ticket.id}>
                  <td><strong>{ticket.subject}</strong></td>
                  <td><span className="cc-status-chip">{statusLabel(ticket.status)}</span></td>
                  <td>{priorityLabel(ticket.priority)}</td>
                  <td>{demoUserLabel(ticket.assignee_id)}</td>
                  <td>
                    <div className="cc-table-actions">
                      <select
                        className="cc-field"
                        aria-label={`为“${ticket.subject}”选择负责人`}
                        value={targets[ticket.id] ?? ""}
                        onChange={(e) =>
                          setTargets((current) => ({
                            ...current,
                            [ticket.id]: e.target.value,
                          }))
                        }
                      >
                        <option value="">选择坐席…</option>
                        {agents.map((agent) => (
                          <option key={agent.id} value={agent.id}>
                            {agent.label}
                          </option>
                        ))}
                      </select>
                      <button
                        className="cc-button cc-button--primary"
                        disabled={
                          assigningId === ticket.id ||
                          !targets[ticket.id] ||
                          targets[ticket.id] === ticket.assignee_id ||
                          ![
                            "triaged",
                            "assigned",
                            "in_progress",
                            "waiting_customer",
                            "reopened",
                          ].includes(ticket.status)
                        }
                        onClick={() => assign(ticket)}
                      >
                        {assigningId === ticket.id
                          ? "保存中…"
                          : ticket.assignee_id
                            ? "重新分配"
                            : "分配"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>

      <section className="cc-ops-stack" aria-label="运营处置模块">
        <HandoffQueuePanel mode="supervisor" />
        {isSupervisor && <QaReviewPanel />}
        {isSupervisor && <ObservabilityPanel />}
      </section>
    </div>
  );
}
