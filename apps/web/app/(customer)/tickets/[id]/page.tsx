"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import AfterSalesCustomerPanel from "@/components/after-sales/AfterSalesCustomerPanel";
import { api } from "@/lib/api";
import {
  priorityLabel,
  statusLabel,
  type Ticket,
  type TicketEvent,
} from "@/lib/tickets";

const EVENT_LABELS: Record<string, string> = {
  created: "工单已创建",
  status_changed: "状态已更新",
  comment_added: "客服回复",
  assigned: "客服已接手",
};

export default function TicketDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [events, setEvents] = useState<TicketEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [t, evs] = await Promise.all([
          api<Ticket>(`/v1/tickets/${id}`),
          api<TicketEvent[]>(`/v1/tickets/${id}/events`),
        ]);
        setTicket(t);
        setEvents(evs);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载失败");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  if (loading) return <p className="p-8">加载中…</p>;
  if (error) return <p className="p-8 text-red-600">{error}</p>;
  if (!ticket) return <p className="p-8 text-gray-500">工单不存在</p>;

  return (
    <div className="p-8 max-w-2xl">
      <h1 className="text-2xl font-bold">{ticket.subject}</h1>
      <p className="text-gray-500 text-sm mb-2">
        状态：{statusLabel(ticket.status)} · 优先级：
        {priorityLabel(ticket.priority)}
      </p>
      {ticket.description && <p className="mb-4">{ticket.description}</p>}

      <AfterSalesCustomerPanel ticketId={ticket.id} />

      <h2 className="font-semibold mb-2">时间线</h2>
      {events.length === 0 ? (
        <p className="text-gray-500">暂无事件</p>
      ) : (
        <ul className="space-y-2">
          {events.map((ev) => (
            <li key={ev.id} className="border rounded p-2 text-sm">
              <span className="text-gray-500">
                {EVENT_LABELS[ev.event_type] ?? ev.event_type}
              </span>
              {ev.payload_digest && <div>{ev.payload_digest}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
