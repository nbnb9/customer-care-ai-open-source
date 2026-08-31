export type DemoUser = {
  id: string;
  label: string;
  tenantId: string;
  role: "customer" | "agent" | "supervisor";
  // UI prefill only. The API binds customer callers to their stored region;
  // staff applicability scope still needs trusted product/order context in a
  // production identity integration.
  defaultKnowledgeRegion: string;
};

export const DEMO_USERS: DemoUser[] = [
  {
    id: "user-nw-customer-01",
    label: "Northwind 客户",
    tenantId: "tenant-northwind-demo",
    role: "customer",
    defaultKnowledgeRegion: "cn-north",
  },
  {
    id: "user-nw-agent-01",
    label: "Northwind 坐席 01",
    tenantId: "tenant-northwind-demo",
    role: "agent",
    defaultKnowledgeRegion: "cn-north",
  },
  {
    id: "user-nw-agent-02",
    label: "Northwind 坐席 02",
    tenantId: "tenant-northwind-demo",
    role: "agent",
    defaultKnowledgeRegion: "cn-north",
  },
  {
    id: "user-nw-supervisor-01",
    label: "Northwind 主管",
    tenantId: "tenant-northwind-demo",
    role: "supervisor",
    defaultKnowledgeRegion: "cn-north",
  },
  {
    id: "user-ct-customer-01",
    label: "Contoso 客户",
    tenantId: "tenant-contoso-demo",
    role: "customer",
    defaultKnowledgeRegion: "cn-east",
  },
  {
    id: "user-ct-agent-01",
    label: "Contoso 坐席 01",
    tenantId: "tenant-contoso-demo",
    role: "agent",
    defaultKnowledgeRegion: "cn-east",
  },
  {
    id: "user-ct-agent-02",
    label: "Contoso 坐席 02",
    tenantId: "tenant-contoso-demo",
    role: "agent",
    defaultKnowledgeRegion: "cn-east",
  },
  {
    id: "user-ct-supervisor-01",
    label: "Contoso 主管",
    tenantId: "tenant-contoso-demo",
    role: "supervisor",
    defaultKnowledgeRegion: "cn-east",
  },
];

export function assignableAgentsFor(userId: string): DemoUser[] {
  const current = DEMO_USERS.find((user) => user.id === userId);
  if (!current) return [];
  return DEMO_USERS.filter(
    (user) => user.tenantId === current.tenantId && user.role === "agent",
  );
}

export function findDemoUser(userId: string): DemoUser | undefined {
  return DEMO_USERS.find((user) => user.id === userId);
}

export function demoUserLabel(userId: string | null): string {
  if (!userId) return "未分配";
  return DEMO_USERS.find((user) => user.id === userId)?.label ?? userId;
}
