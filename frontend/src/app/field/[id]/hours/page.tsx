"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getProject } from "@/lib/projects";
import {
  type TimeEntry,
  currentLocation,
  getActivePunch,
  punchIn,
  punchOut,
} from "@/lib/timeTracking";

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** Precise shift length from the two timestamps (so a short shift never reads "0:00"). */
function fmtDuration(inISO?: string | null, outISO?: string | null): string {
  if (!inISO || !outISO) return "—";
  const s = Math.max(0, Math.round((new Date(outISO).getTime() - new Date(inISO).getTime()) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m} min`;
  return `${sec} sec`;
}

function fmtDist(m?: number | null): string {
  if (m == null) return "";
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

export default function HoursClock() {
  const { id } = useParams<{ id: string }>();
  const [name, setName] = useState("");
  const [active, setActive] = useState<TimeEntry | null>(null);
  const [done, setDone] = useState<TimeEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locWarn, setLocWarn] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState("");

  const load = useCallback(async () => {
    getProject(id).then((p) => setName(p.name)).catch(() => {});
    try {
      const r = await getActivePunch();
      setActive(r.active);
    } catch (e) {
      setError(String(e));
    }
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!active || active.status !== "open" || !active.punch_in_at) {
      setElapsed("");
      return;
    }
    const start = new Date(active.punch_in_at).getTime();
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - start) / 1000));
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      setElapsed(`${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`);
    };
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
  }, [active]);

  const doPunchIn = async () => {
    setBusy(true);
    setError(null);
    setDone(null);
    setLocWarn(null);
    try {
      const loc = await currentLocation();
      const e = await punchIn(id, loc);
      setActive(e);
      if (e.location_flag) {
        setLocWarn(`You punched in about ${fmtDist(e.distance_in_m)} from the job site — your location was recorded.`);
      } else if (loc.lat == null) {
        setLocWarn("Location wasn't available — the punch was recorded without GPS.");
      }
    } catch (err) {
      const x = err as { status?: number; body?: { detail?: { active?: TimeEntry } } };
      if (x.status === 409 && x.body?.detail?.active) {
        setActive(x.body.detail.active);
        setError(
          `You're already clocked in at ${x.body.detail.active.project_name ?? "another project"}. Punch out there first.`,
        );
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const doPunchOut = async () => {
    setBusy(true);
    setError(null);
    try {
      const loc = await currentLocation();
      const e = await punchOut(loc);
      setDone(e);
      setActive(null);
      setLocWarn(
        e.location_flag
          ? `You punched out about ${fmtDist(e.distance_out_m)} from the job site — your location was recorded.`
          : null,
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const clockedInHere = active?.status === "open" && active.project_id === id;
  const clockedInElsewhere = active?.status === "open" && active.project_id !== id;

  return (
    <div className="space-y-4">
      <Link href={`/field/${id}`} className="text-xs text-slate-500">← Back</Link>
      <h1 className="px-1 text-lg font-semibold text-slate-900">{name || "Project"} — Hours</h1>

      {error && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">{error}</div>
      )}
      {locWarn && (
        <div className="rounded-lg border border-orange-300 bg-orange-50 p-3 text-sm text-orange-800">
          📍 {locWarn}
        </div>
      )}

      {done && (
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-center">
          <div className="text-sm text-emerald-800">Shift recorded</div>
          <div className="text-3xl font-bold text-emerald-700">
            {fmtDuration(done.punch_in_at, done.punch_out_at)}
          </div>
          <div className="text-xs text-emerald-700">
            {fmtTime(done.punch_in_at)} → {fmtTime(done.punch_out_at)}
          </div>
        </div>
      )}

      {clockedInHere ? (
        <div className="rounded-xl border border-sky-300 bg-sky-50 p-6 text-center">
          <div className="text-sm text-sky-800">On the clock since {fmtTime(active!.punch_in_at)}</div>
          <div className="my-2 text-4xl font-bold tabular-nums text-sky-700">{elapsed}</div>
          {active!.punch_in_loc && <div className="text-[11px] text-sky-700">📍 Location captured</div>}
          <button
            onClick={doPunchOut}
            disabled={busy}
            className="mt-3 w-full rounded-lg bg-red-600 py-4 text-lg font-semibold text-white disabled:opacity-50"
          >
            {busy ? "…" : "Punch Out"}
          </button>
        </div>
      ) : clockedInElsewhere ? (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-6 text-center">
          <div className="text-sm text-amber-800">
            You&rsquo;re clocked in at <b>{active!.project_name ?? "another project"}</b>.
          </div>
          <p className="mt-1 text-xs text-amber-700">Punch out there before starting here.</p>
          <Link
            href={`/field/${active!.project_id}/hours`}
            className="mt-3 inline-block rounded-lg border border-amber-400 px-4 py-2 text-sm font-medium text-amber-800"
          >
            Go to that project
          </Link>
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
          <p className="text-sm text-slate-500">Not clocked in.</p>
          <button
            onClick={doPunchIn}
            disabled={busy}
            className="mt-3 w-full rounded-lg bg-[#0068A0] py-4 text-lg font-semibold text-white disabled:opacity-50"
          >
            {busy ? "…" : "Punch In"}
          </button>
          <p className="mt-2 text-[11px] text-slate-400">
            Your location is recorded at punch-in and punch-out.
          </p>
        </div>
      )}
    </div>
  );
}
