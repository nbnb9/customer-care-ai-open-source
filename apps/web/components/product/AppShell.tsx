"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import DemoSwitcher from "@/components/DemoSwitcher";

type ShellRole = "customer" | "agent" | "supervisor";

type NavigationItem = {
  href: string;
  label: string;
  description: string;
};

const ROLE_CONFIG: Record<
  ShellRole,
  {
    eyebrow: string;
    title: string;
    user: string;
    initials: string;
    navigation: NavigationItem[];
  }
> = {
  customer: {
    eyebrow: "Aster Audio 客户服务",
    title: "服务中心",
    user: "已验证客户",
    initials: "客",
    navigation: [
      { href: "/support", label: "服务首页", description: "会话与服务进度" },
      {
        href: "/support/order-lookup",
        label: "订单与保修",
        description: "受控只读查询",
      },
    ],
  },
  agent: {
    eyebrow: "Northwind Audio · 售后团队",
    title: "CareOps",
    user: "售后坐席",
    initials: "坐",
    navigation: [
      { href: "/workspace", label: "协同工作台", description: "队列、会话与上下文" },
    ],
  },
  supervisor: {
    eyebrow: "Northwind Audio · 运营沙箱",
    title: "CareOps",
    user: "售后主管",
    initials: "主",
    navigation: [
      { href: "/queue", label: "运营概览", description: "队列、质量与风险" },
    ],
  },
};

function isCurrent(pathname: string, href: string): boolean {
  if (href === "/support") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function AppShell({
  role,
  children,
  showDemoTools = false,
}: {
  role: ShellRole;
  children: ReactNode;
  showDemoTools?: boolean;
}) {
  const pathname = usePathname();
  const config = ROLE_CONFIG[role];
  const customer = role === "customer";

  return (
    <div className={`cc-app-shell cc-app-shell--${role}`}>
      <a className="cc-skip-link" href="#cc-main-content">
        跳到主要内容
      </a>
      {showDemoTools && (
        <div className="cc-demo-toolbar" role="note">
          <span className="cc-environment-badge">合成开发环境</span>
          <span>身份仅用于本机演示，不是生产登录。</span>
          <DemoSwitcher />
        </div>
      )}

      <div className={customer ? "cc-customer-shell" : "cc-staff-shell"}>
        {!customer && (
          <aside className="cc-sidebar" aria-label={`${config.user}导航`}>
            <Link className="cc-brand" href="/" aria-label="CareOps 首页">
              <span className="cc-brand-mark" aria-hidden="true">CO</span>
              <span>
                <strong>{config.title}</strong>
                <small>可信售后协同</small>
              </span>
            </Link>

            <p className="cc-sidebar-label">工作空间</p>
            <nav className="cc-side-navigation">
              {config.navigation.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
                >
                  <span>{item.label}</span>
                  <small>{item.description}</small>
                </Link>
              ))}
            </nav>

            <div className="cc-sidebar-account">
              <span className="cc-avatar" aria-hidden="true">{config.initials}</span>
              <span>
                <strong>{config.user}</strong>
                <small>{config.eyebrow}</small>
              </span>
            </div>
          </aside>
        )}

        <div className="cc-shell-main">
          <header className="cc-topbar">
            {customer ? (
              <Link className="cc-customer-brand" href="/support">
                <span className="cc-brand-mark" aria-hidden="true">AA</span>
                <span>
                  <strong>{config.eyebrow}</strong>
                  <small>订单、保修与人工服务</small>
                </span>
              </Link>
            ) : (
              <div>
                <p className="cc-topbar-eyebrow">{config.eyebrow}</p>
                <p className="cc-topbar-title">
                  {role === "agent" ? "坐席协同台" : "售后运营台"}
                </p>
              </div>
            )}

            {customer && (
              <nav className="cc-customer-navigation" aria-label="客户服务导航">
                {config.navigation.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            )}

            <div className="cc-topbar-user" aria-label={`当前角色：${config.user}`}>
              <span className="cc-avatar" aria-hidden="true">{config.initials}</span>
              <span>{config.user}</span>
            </div>
          </header>
          <main id="cc-main-content" className="cc-shell-content" tabIndex={-1}>
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
