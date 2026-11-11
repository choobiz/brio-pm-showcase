// User management (superadmin) — client-side via the /api/backend proxy.

const BASE = "/api/backend";

export const ROLES = ["superadmin", "manager", "worker", "owner"] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABEL: Record<string, string> = {
  superadmin: "Superadmin",
  manager: "Manager",
  worker: "Worker",
  owner: "Owner (client)",
};

export interface AppUser {
  id: string;
  email: string;
  name: string | null;
  role: string;
  active: boolean;
  created_at: string | null;
  project_ids: string[];
}

export interface Me {
  id: string;
  email: string;
  role: string;
  name: string | null;
  rank: number;
  project_ids: string[];
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const err = new Error(`API ${res.status}`) as Error & { status: number };
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? (undefined as T) : (res.json() as Promise<T>);
}

export const getMe = () => api<Me>("/users/me");
export const listUsers = () => api<AppUser[]>("/users");

export const createUser = (body: {
  email: string;
  name?: string;
  role: string;
  project_ids?: string[];
}) => api<AppUser>("/users", { method: "POST", body: JSON.stringify(body) });

export const updateUser = (
  id: string,
  body: Partial<{ name: string; role: string; active: boolean; project_ids: string[] }>,
) => api<AppUser>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const deactivateUser = (id: string) =>
  api<{ ok: boolean }>(`/users/${id}`, { method: "DELETE" });

// Lightweight project list for assignment UI.
export const listProjectsLite = () =>
  api<{ id: string; name: string }[]>("/projects");
