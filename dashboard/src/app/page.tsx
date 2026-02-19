"use client";

import Link from "next/link";

export default function Home() {
  return (
    <main style={{ padding: "2rem", maxWidth: 800, margin: "0 auto" }}>
      <h1>Defense System Dashboard</h1>
      <p>Autonomous AI Defense for Critical Infrastructure. Sign in to access operator views.</p>
      <nav style={{ marginTop: "1.5rem", display: "flex", gap: "1rem" }}>
        <Link href="/login">Login</Link>
        <Link href="/dashboard">Dashboard (requires auth)</Link>
      </nav>
    </main>
  );
}
