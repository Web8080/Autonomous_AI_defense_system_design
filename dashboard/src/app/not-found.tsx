"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function NotFound() {
  useEffect(() => {
    window.location.replace("/login");
  }, []);

  return (
    <main style={{ padding: "2rem", maxWidth: 560, margin: "4rem auto", textAlign: "center" }}>
      <h1 style={{ marginBottom: "0.5rem" }}>Page not found</h1>
      <p style={{ color: "#8b949e", marginBottom: "1rem" }}>
        Redirecting to login…
      </p>
      <p style={{ color: "#8b949e", marginBottom: "1.5rem", fontSize: 14 }}>
        If nothing happens, use a link:
      </p>
      <nav style={{ display: "flex", flexDirection: "column", gap: "0.75rem", alignItems: "center" }}>
        <Link href="/login">Login</Link>
        <Link href="/dashboard">Dashboard</Link>
        <Link href="/dashboard/simulation">Simulation</Link>
      </nav>
    </main>
  );
}
