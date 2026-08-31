import type { ReactNode } from "react";
import AppShell from "@/components/product/AppShell";

export default function SupervisorLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell role="supervisor" showDemoTools={process.env.NODE_ENV !== "production"}>
      {children}
    </AppShell>
  );
}
