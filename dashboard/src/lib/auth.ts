/**
 * Session and role helpers. Token stored in localStorage; role in sessionStorage.
 * Replace with proper auth provider (e.g. NextAuth) for production.
 */

export type Role = "super_admin" | "local_operator" | "system_ai";

const ROLE_KEY = "defense_role";

export function setSession(token: string, role: Role): void {
  if (typeof window === "undefined") return;
  localStorage.setItem("defense_token", token);
  sessionStorage.setItem(ROLE_KEY, role);
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem("defense_token");
  sessionStorage.removeItem(ROLE_KEY);
}

export function getRole(): Role | null {
  if (typeof window === "undefined") return null;
  const r = sessionStorage.getItem(ROLE_KEY);
  return r as Role | null;
}

export function isSuperAdmin(): boolean {
  return getRole() === "super_admin";
}

export function isLocalOperator(): boolean {
  return getRole() === "local_operator";
}

export function hasAccessToRegion(regionId: string): boolean {
  if (isSuperAdmin()) return true;
  const regions = sessionStorage.getItem("defense_region_ids");
  if (!regions) return false;
  return regions.split(",").includes(regionId);
}
