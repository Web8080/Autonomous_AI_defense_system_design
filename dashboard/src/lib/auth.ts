/**
 * Session handling. Access tokens are short-lived (15m) and refreshed via the
 * refresh token. Role and site scoping come from the verified JWT claims returned
 * by the auth service; they are never chosen by the client.
 */

export type Role = "super_admin" | "local_operator" | "system_ai";

const ACCESS_KEY = "dis_access_token";
const REFRESH_KEY = "dis_refresh_token";
const USER_KEY = "dis_user";

export type SessionUser = {
  id: string;
  email: string;
  role: Role;
  org_id: string | null;
  site_ids: string[];
};

export type LoginResponse = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: SessionUser;
};

function apiUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
}

export async function login(email: string, password: string): Promise<SessionUser> {
  const res = await fetch(`${apiUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? "Invalid credentials");
  }
  const data: LoginResponse = await res.json();
  setSession(data);
  return data.user;
}

export function setSession(data: LoginResponse): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, data.access_token);
  localStorage.setItem(REFRESH_KEY, data.refresh_token);
  localStorage.setItem(USER_KEY, JSON.stringify(data.user));
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function getUser(): SessionUser | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SessionUser;
  } catch {
    return null;
  }
}

/** Exchange the refresh token for a new access token. Returns false if the session is dead. */
export async function refreshSession(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  const res = await fetch(`${apiUrl()}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) {
    clearSession();
    return false;
  }
  const data = await res.json();
  if (typeof window !== "undefined") {
    localStorage.setItem(ACCESS_KEY, data.access_token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
  }
  return true;
}

export async function logout(): Promise<void> {
  const refresh = getRefreshToken();
  if (refresh) {
    await fetch(`${apiUrl()}/api/v1/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    }).catch(() => undefined);
  }
  clearSession();
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getRole(): Role | null {
  return getUser()?.role ?? null;
}

export function isSuperAdmin(): boolean {
  return getRole() === "super_admin";
}

export function isLocalOperator(): boolean {
  return getRole() === "local_operator";
}

/**
 * Client-side convenience only. The authoritative check is server-side, where
 * the gateway derives scoping from the signed token, not from anything the
 * browser sends.
 */
export function hasAccessToSite(siteId: string): boolean {
  const user = getUser();
  if (!user) return false;
  if (user.role === "super_admin") return true;
  return user.site_ids.includes(siteId);
}
