"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";

type OrderStatusRead = {
  order_number: string;
  status: string;
  ordered_at: string;
};

type WarrantyEligibilityRead = {
  order_number: string;
  eligible: boolean;
  coverage_ends_at: string | null;
};

const ORDER_STATUS_LABELS: Record<string, string> = {
  processing: "处理中",
  shipped: "已发货",
  delivered: "已送达",
  cancelled: "已取消",
  fulfilled: "已履约",
  partially_fulfilled: "部分履约",
  unfulfilled: "待履约",
  in_progress: "履约处理中",
  on_hold: "履约暂停",
  scheduled: "已安排履约",
  restocked: "已重新入库",
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date);
}

function lookupErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "未找到属于当前已验证账户的订单。请核对订单号，或转人工客服。";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "此查询仅面向已验证客户账户。请转人工客服协助。";
  }
  return error instanceof Error ? error.message : "查询失败，请稍后重试或转人工客服。";
}

export default function OrderLookupPage() {
  const [orderNumber, setOrderNumber] = useState("");
  const [orderResult, setOrderResult] = useState<OrderStatusRead | null>(null);
  const [warrantyResult, setWarrantyResult] =
    useState<WarrantyEligibilityRead | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState<"order" | "warranty" | null>(null);

  async function lookup(kind: "order" | "warranty"): Promise<void> {
    const candidate = orderNumber.trim();
    if (!candidate) {
      setError("请输入订单号。");
      return;
    }

    setError("");
    setOrderResult(null);
    setWarrantyResult(null);
    setLoading(kind);
    try {
      if (kind === "order") {
        setOrderResult(
          await api<OrderStatusRead>("/v1/customer-tools/order-status", {
            method: "POST",
            body: JSON.stringify({ order_number: candidate }),
          }),
        );
      } else {
        setWarrantyResult(
          await api<WarrantyEligibilityRead>(
            "/v1/customer-tools/warranty-eligibility",
            {
              method: "POST",
              body: JSON.stringify({ order_number: candidate }),
            },
          ),
        );
      }
    } catch (lookupError) {
      setError(lookupErrorMessage(lookupError));
    } finally {
      setLoading(null);
    }
  }

  function submitOrderStatus(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void lookup("order");
  }

  return (
    <div className="cc-page cc-page--narrow cc-lookup-page">
      <header className="cc-page-header">
        <div>
          <p className="cc-page-kicker">Verified read-only service</p>
          <h1 className="cc-page-title">订单与保修查询</h1>
          <p className="cc-page-description">
            输入订单号，查询当前已验证账户可访问的最小订单和保修状态。此入口不依赖模型，
            也不会修改订单、支付、保修或账户资料。
          </p>
        </div>
        <span className="cc-status-chip">只读 · 不产生外部动作</span>
      </header>

      <section className="cc-panel cc-lookup-card">
        <div className="cc-panel-header">
          <div>
            <h2>核对订单</h2>
            <p className="cc-help-text">归属由服务端身份映射复核，不接受浏览器提交客户或租户。</p>
          </div>
        </div>
        <form
          className="cc-panel-body cc-form-stack"
          aria-label="订单与保修传统查询"
          onSubmit={submitOrderStatus}
        >
          <label className="cc-field-label" htmlFor="order-number">
            <span>订单号</span>
            <input
              id="order-number"
              aria-label="订单号"
              autoComplete="off"
              className="cc-field"
              placeholder="例如：NW-DEMO-ORDER-1001"
              value={orderNumber}
              onChange={(event) => {
                setOrderNumber(event.target.value);
                setError("");
              }}
            />
          </label>
          <div className="cc-inline-actions">
            <button
              className="cc-button cc-button--primary"
              type="submit"
              disabled={loading !== null}
            >
              {loading === "order" ? "查询中…" : "查询订单状态"}
            </button>
            <button
              className="cc-button cc-button--secondary"
              type="button"
              disabled={loading !== null}
              onClick={() => void lookup("warranty")}
            >
              {loading === "warranty" ? "查询中…" : "查询保修资格"}
            </button>
          </div>
        </form>
      </section>

      {error && <p className="cc-alert cc-alert--danger" role="alert">{error}</p>}

      {orderResult && (
        <section className="cc-panel cc-result-card" aria-label="订单状态结果">
          <div className="cc-panel-header">
            <h2>订单状态</h2>
            <span className="cc-status-chip">
              {ORDER_STATUS_LABELS[orderResult.status] ?? orderResult.status}
            </span>
          </div>
          <dl className="cc-panel-body cc-result-list">
            <div>
              <dt>订单号</dt>
              <dd>{orderResult.order_number}</dd>
            </div>
            <div>
              <dt>订单状态：</dt>
              <dd>{ORDER_STATUS_LABELS[orderResult.status] ?? orderResult.status}</dd>
            </div>
            <div>
              <dt>下单时间</dt>
              <dd>{formatTimestamp(orderResult.ordered_at)}（UTC）</dd>
            </div>
          </dl>
        </section>
      )}

      {warrantyResult && (
        <section className="cc-panel cc-result-card" aria-label="保修资格结果">
          <div className="cc-panel-header">
            <h2>保修资格</h2>
            <span className="cc-status-chip">
              {warrantyResult.eligible ? "当前有效" : "未匹配有效保修"}
            </span>
          </div>
          <div className="cc-panel-body">
            <p>订单号：{warrantyResult.order_number}</p>
            {warrantyResult.eligible ? (
              <p>
                当前订单具备保修资格
                {warrantyResult.coverage_ends_at
                  ? `，保修截至 ${formatTimestamp(warrantyResult.coverage_ends_at)}（UTC）`
                  : "。"}
              </p>
            ) : (
              <p>当前订单没有匹配的有效保修。</p>
            )}
          </div>
        </section>
      )}

      <aside className="cc-lookup-help">
        <div>
          <strong>查询不到结果，或需要修改订单？</strong>
          <p>为保护账户信息，未找到与不属于当前账户使用相同提示。需要写操作时请交由人工确认。</p>
        </div>
        <Link className="cc-button cc-button--secondary" href="/support">
          返回客户支持
        </Link>
      </aside>
    </div>
  );
}
