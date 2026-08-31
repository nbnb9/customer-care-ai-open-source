import Link from "next/link";

export default function Home() {
  return (
    <main className="cc-home">
      <nav className="cc-home-nav" aria-label="产品入口">
        <div className="cc-home-brand">
          <span className="cc-brand-mark" aria-hidden="true">CO</span>
          <span>CareOps</span>
        </div>
        <span className="cc-home-stage">步骤 41.5 · 产品化验证环境</span>
      </nav>

      <div className="cc-home-hero">
        <section>
          <p className="cc-home-kicker">Governed after-sales operations</p>
          <h1>让每一次售后动作，都有依据、有确认、有回执。</h1>
          <p className="cc-home-lead">
            面向消费电子与智能硬件团队的售后协同层。把客户会话、订单与保修、政策证据和人工审批
            放在一个任务上下文中，同时让不确定的外部结果安全停在调查状态。
          </p>
          <div className="cc-home-actions">
            <Link className="cc-button cc-button--primary" href="/workspace">
              进入坐席协同台
            </Link>
            <Link className="cc-button cc-button--secondary" href="/support">
              查看客户服务入口
            </Link>
            <Link className="cc-button cc-button--secondary" href="/queue">
              查看主管运营台
            </Link>
          </div>
        </section>

        <section className="cc-home-proof" aria-label="受控售后流程示例">
          <div className="cc-home-proof-header">
            <strong>补发申请 · AS-2048</strong>
            <span>Northwind Audio</span>
          </div>
          <div className="cc-home-proof-body">
            <div className="cc-home-proof-item">
              <strong>订单与客户归属已核验</strong>
              <span>服务端身份映射 · 最小订单字段</span>
              <em>已验证</em>
            </div>
            <div className="cc-home-proof-item">
              <strong>保修政策与证据版本</strong>
              <span>CN 售后政策 v3.2 · 当前有效</span>
              <em>证据充分</em>
            </div>
            <div className="cc-home-proof-item">
              <strong>下一步：请求客户确认</strong>
              <span>尚未调用 OMS；关键内容变更会清除旧确认。</span>
              <em>安全等待</em>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
