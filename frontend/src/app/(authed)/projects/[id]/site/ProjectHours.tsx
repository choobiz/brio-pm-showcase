"use client";

import { errorMessage } from "@/lib/workspaceApi";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import {
  type GeoLoc,
  type TimeEntry,
  listTimeEntries,
} from "@/lib/timeTracking";

function fmtDT(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
const mapLink = (loc: GeoLoc | null) =>
  loc ? `https://www.google.com/maps?q=${loc.lat},${loc.lng}` : null;

function fmtDist(m: number | null): string | null {
  if (m === null) return null;
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

type HoursData = {
  entries: TimeEntry[];
  total_hm: string | null;
  site: { lat: number; lng: number } | null;
  offsite_radius_m: number;
};

export default function ProjectHours() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<HoursData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    listTimeEntries(id)
      .then((d) =>
        setData({ entries: d.entries, total_hm: d.total_hm, site: d.site, offsite_radius_m: d.offsite_radius_m }),
      )
      .catch((e) => setErr(errorMessage(e)));
  }, [id]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Hours
        </h1>
        {data && (
          <div className="text-sm text-slate-500">
            Total: <b>{data.total_hm ?? "0:00"}</b>
          </div>
        )}
      </div>
      {err && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>
      )}
      {data && !data.site && data.entries.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Off-site flagging is off — this project&apos;s address couldn&apos;t be geocoded. Add or
          correct the address in the project settings to enable distance checks.
        </div>
      )}
      {data?.site && (
        <div className="text-xs text-slate-500">
          Distances measured from the job site · punches beyond{" "}
          {fmtDist(data.offsite_radius_m)} are flagged{" "}
          <span className="text-red-600">⚠</span>.
        </div>
      )}
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">Employee</th>
              <th className="px-4 py-2">In</th>
              <th className="px-4 py-2">Out</th>
              <th className="px-4 py-2 text-right">Hours</th>
              <th className="px-4 py-2">Location</th>
            </tr>
          </thead>
          <tbody>
            {data?.entries.map((e) => (
              <tr
                key={e.id}
                className={`border-t border-slate-100 ${e.location_flag ? "bg-red-50/60" : ""}`}
              >
                <td className="px-4 py-2">
                  <span className="inline-flex items-center gap-1.5">
                    {e.location_flag && (
                      <span title="Off-site punch — see distance" className="text-red-600">
                        ⚠
                      </span>
                    )}
                    {e.user}
                  </span>
                </td>
                <td className="px-4 py-2 text-slate-600">{fmtDT(e.punch_in_at)}</td>
                <td className="px-4 py-2 text-slate-600">
                  {e.status === "open" ? (
                    <span className="text-emerald-600">on the clock</span>
                  ) : (
                    fmtDT(e.punch_out_at)
                  )}
                </td>
                <td className="px-4 py-2 text-right font-medium">{e.hours_hm ?? "—"}</td>
                <td className="px-4 py-2 text-xs">
                  {e.punch_in_loc ? (
                    <a href={mapLink(e.punch_in_loc)!} target="_blank" rel="noopener noreferrer" className="text-[var(--color-brio-primary)] hover:underline">
                      in 📍
                    </a>
                  ) : (
                    <span className="text-slate-400">no GPS</span>
                  )}
                  {e.distance_in_m !== null && (
                    <span className={e.distance_in_m > data!.offsite_radius_m ? "ml-1 text-red-600 font-medium" : "ml-1 text-slate-400"}>
                      ({fmtDist(e.distance_in_m)})
                    </span>
                  )}
                  {e.punch_out_loc && (
                    <>
                      {" · "}
                      <a href={mapLink(e.punch_out_loc)!} target="_blank" rel="noopener noreferrer" className="text-[var(--color-brio-primary)] hover:underline">
                        out 📍
                      </a>
                      {e.distance_out_m !== null && (
                        <span className={e.distance_out_m > data!.offsite_radius_m ? "ml-1 text-red-600 font-medium" : "ml-1 text-slate-400"}>
                          ({fmtDist(e.distance_out_m)})
                        </span>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
            {data && data.entries.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  No time entries yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
