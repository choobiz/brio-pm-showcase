// Project messages — office side, via the /api/backend proxy.

const BASE = "/api/backend";

export interface ProjectMessage {
  id: string;
  sender_role: "owner" | "office";
  body: string;
  read_at: string | null;
  created_at: string | null;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.status === 204 ? (undefined as T) : (r.json() as Promise<T>);
}

const base = (pid: string) => `/projects/${pid}/messages`;

export const listMessages = (pid: string) =>
  api<{ messages: ProjectMessage[] }>(base(pid));

export const sendMessage = (pid: string, body: string) =>
  api<ProjectMessage>(base(pid), { method: "POST", body: JSON.stringify({ body }) });

/** Count of unread client messages (for the in-project unread badge). */
export const getUnreadCount = (pid: string) =>
  api<{ count: number }>(`${base(pid)}/unread`);
