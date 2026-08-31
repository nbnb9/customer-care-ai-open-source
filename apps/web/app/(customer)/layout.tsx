import type { ReactNode } from "react";
import AppShell from "@/components/product/AppShell";

export default function CustomerLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell role="customer" showDemoTools={process.env.NODE_ENV !== "production"}>
      {children}
    </AppShell>
  );
}
