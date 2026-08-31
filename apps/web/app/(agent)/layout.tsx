import type { ReactNode } from "react";
import AppShell from "@/components/product/AppShell";

export default function AgentLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell role="agent" showDemoTools={process.env.NODE_ENV !== "production"}>
      {children}
    </AppShell>
  );
}
