"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { setSession } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"super_admin" | "local_operator">("super_admin");
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiUrl}/health`);
      if (!res.ok) throw new Error("API unreachable");
      const useDevToken =
        apiUrl.includes("localhost") || apiUrl.includes("127.0.0.1");
      setSession(useDevToken ? "dev-token" : "stub-token-" + Date.now(), role);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  };

  return (
    <main style={{ padding: "2rem", maxWidth: 400, margin: "0 auto" }}>
      <h1>Sign in</h1>
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={{ display: "block", width: "100%", marginTop: 4 }}
          />
        </div>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ display: "block", width: "100%", marginTop: 4 }}
          />
        </div>
        <div style={{ marginBottom: "1rem" }}>
          <label htmlFor="role">Role</label>
          <select
            id="role"
            value={role}
            onChange={(e) => setRole(e.target.value as "super_admin" | "local_operator")}
            style={{ display: "block", width: "100%", marginTop: 4 }}
          >
            <option value="super_admin">Super Admin</option>
            <option value="local_operator">Local Operator</option>
          </select>
        </div>
        {error && <p style={{ color: "#f85149", marginBottom: "1rem" }}>{error}</p>}
        <button type="submit">Sign in</button>
      </form>
      <p style={{ marginTop: "1rem", fontSize: 14 }}>
        Local dev: uses dev-token when API is localhost (set ALLOW_DEV_TOKEN=true on gateway). Prod: use real JWT from auth service.
      </p>
    </main>
  );
}
