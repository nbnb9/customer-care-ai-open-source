"use client";

import { useEffect, useState } from "react";
import { DEMO_USERS } from "@/lib/demo-users";

export default function DemoSwitcher() {
  const [userId, setUserId] = useState("user-nw-customer-01");

  useEffect(() => {
    setUserId(localStorage.getItem("demo-user-id") ?? "user-nw-customer-01");
  }, []);

  return (
    <label className="cc-demo-identity">
      <span>合成身份</span>
      <select
        aria-label="合成演示身份"
        value={userId}
        onChange={(e) => {
          localStorage.setItem("demo-user-id", e.target.value);
          window.location.reload();
        }}
      >
        {DEMO_USERS.map((u) => (
          <option key={u.id} value={u.id}>
            {u.label}
          </option>
        ))}
      </select>
    </label>
  );
}
