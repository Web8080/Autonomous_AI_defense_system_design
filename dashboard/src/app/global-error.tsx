"use client";

import { useEffect } from "react";

export default function GlobalError({
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
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, padding: "2rem", background: "#0d1117", color: "#c9d1d9" }}>
        <main style={{ maxWidth: 560, margin: "4rem auto", textAlign: "center" }}>
          <h1 style={{ marginBottom: "0.5rem" }}>Something went wrong</h1>
          <p style={{ color: "#8b949e", marginBottom: "1.5rem" }}>
            A critical error occurred. Try again or refresh the page.
          </p>
          <button type="button" onClick={reset} style={{ padding: "0.5rem 1rem", cursor: "pointer" }}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
