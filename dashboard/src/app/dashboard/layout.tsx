"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getRole, clearSession } from "@/lib/auth";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [role, setRole] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setRole(getRole());
    setMounted(true);
  }, []);

  if (!mounted) return null;
  if (!role) {
    return (
      <main style={{ padding: "2rem" }}>
        <p>Not signed in.</p>
        <Link href="/login">Go to login</Link>
      </main>
    );
  }

  return (
    <div>
      <header
        style={{
          borderBottom: "1px solid #30363d",
          padding: "0.75rem 1.5rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <nav style={{ display: "flex", gap: "1rem" }}>
          <Link href="/dashboard">Map</Link>
          <Link href="/dashboard/assets">Assets</Link>
          <Link href="/dashboard/alerts">Alerts</Link>
          <Link href="/dashboard/control">Control</Link>
          {role === "super_admin" && <Link href="/dashboard/admin">Admin</Link>}
        </nav>
        <span style={{ fontSize: 14 }}>Role: {role}</span>
        <button type="button" onClick={() => { clearSession(); window.location.href = "/"; }}>
          Sign out
        </button>
      </header>
      {children}
    </div>
  );
}
