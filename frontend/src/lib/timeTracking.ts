// Employee time tracking — punch in/out with GPS.

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    // Surface the JSON body (409 conflict carries {detail:{error, active}}).
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    const err = new Error(`API ${res.status}`) as Error & { status: number; body: unknown };
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return res.json() as Promise<T>;
}

export interface GeoLoc {
  lat: number;
  lng: number;
  accuracy: number | null;
}

export interface TimeEntry {
  id: string;
  user: string | null;
  user_id: string;
  project_id: string;
  project_name: string | null;
  status: "open" | "closed";
  punch_in_at: string | null;
  punch_in_loc: GeoLoc | null;
  punch_out_at: string | null;
  punch_out_loc: GeoLoc | null;
  distance_in_m: number | null;
  distance_out_m: number | null;
  location_flag: boolean;
  hours: number | null;
  hours_hm: string | null;
  notes: string | null;
  source: string;
}

export const getActivePunch = () =>
  api<{ active: TimeEntry | null }>(`/me/active-punch`);

export const punchIn = (projectId: string, loc: Partial<GeoLoc>, notes?: string) =>
  api<TimeEntry>(`/projects/${projectId}/punch-in`, {
    method: "POST",
    body: JSON.stringify({ ...loc, notes }),
  });

export const punchOut = (loc: Partial<GeoLoc>, notes?: string) =>
  api<TimeEntry>(`/me/punch-out`, {
    method: "POST",
    body: JSON.stringify({ ...loc, notes }),
  });

export const listTimeEntries = (projectId: string) =>
  api<{
    entries: TimeEntry[];
    total_hours: number;
    total_hm: string | null;
    site: { lat: number; lng: number } | null;
    offsite_radius_m: number;
  }>(`/projects/${projectId}/time-entries`);

/** Best-effort current GPS; resolves to {} if unavailable/denied (we record but
 *  never block a punch on missing location). */
export function currentLocation(timeoutMs = 8000): Promise<Partial<GeoLoc>> {
  return new Promise((resolve) => {
    if (typeof navigator === "undefined" || !navigator.geolocation) return resolve({});
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy ?? null,
        }),
      () => resolve({}),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 0 },
    );
  });
}
