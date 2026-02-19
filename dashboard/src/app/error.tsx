"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main style={{ padding: "2rem", maxWidth: 560, margin: "4rem auto", textAlign: "center" }}>
      <h1 style={{ marginBottom: "0.5rem" }}>Something went wrong</h1>
      <p style={{ color: "#8b949e", marginBottom: "1.5rem" }}>
        An error occurred. You can try again or go back to a safe page.
      </p>
      <div style={{ display: "flex", gap: "1rem", justifyContent: "center", flexWrap: "wrap" }}>
        <button type="button" onClick={reset} style={{ padding: "0.5rem 1rem" }}>
          Try again
        </button>
        <Link href="/login" style={{ padding: "0.5rem 1rem" }}>
          Go to login
        </Link>
        <Link href="/dashboard" style={{ padding: "0.5rem 1rem" }}>
          Dashboard
        </Link>
      </div>
    </main>
  );
}
