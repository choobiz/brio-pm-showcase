"use client";

import { useState } from "react";

import { Box, Label, Tag } from "@/components/kit";
import { confirmGeometry, type Geometry, type GeometryState } from "@/lib/prep";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * The foundation geometry every forming quantity is counted from (2026-10-13).
 * The AI proposes it from the survey and drawings; a person checks it against
 * the markers and confirms it once. From then on the calculator gives the same
 * quantities every time, on the Mac or the server.
 */

const BLANK: Geometry = {
  faces: [], outside_corners: 0, inside_corners: 0, wall_thickness_in: 8, footing_width_in: 24,
  footing_depth_in: 8, wall_height_ft: 3, interior_walls: [], t_junctions: 0, footing_bars: 2,
  bar_size: "15M", lap_in: 26, bar_stock_ft: 20, notes: "",
};

const ftIn = (ft: number) => {
  const inches = Math.round(ft * 12 * 8) / 8;
  return `${Math.floor(inches / 12)}' ${(inches % 12).toFixed(inches % 1 ? 2 : 0)}"`;
};

export function GeometryCard({ projectId, state, editable, onConfirmed }: {
  projectId: string;
  state: GeometryState;
  editable: boolean;
  onConfirmed: (g: GeometryState) => void;
}) {
  const [g, setG] = useState<Geometry>({ ...BLANK, ...state.payload } as Geometry);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const confirmedNow = state.status === "confirmed";
  const perimeter = g.faces.reduce((a, f) => a + (Number(f.length_ft) || 0), 0);
  const cornerProblem =
    g.outside_corners - g.inside_corners !== 4
      ? `Outside corners must be 4 more than inside corners (now ${g.outside_corners} and ${g.inside_corners}).`
      : g.outside_corners + g.inside_corners !== g.faces.length
        ? `${g.faces.length} faces need ${g.faces.length} corners (now ${g.outside_corners + g.inside_corners}).`
        : null;

  const num = (k: keyof Geometry) => (e: React.ChangeEvent<HTMLInputElement>) => setG({ ...g, [k]: Number(e.target.value) });
  const confirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      onConfirmed(await confirmGeometry(projectId, g));
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const box = "rounded border border-slate-300 px-1.5 py-1 text-sm disabled:border-transparent disabled:bg-transparent";
  const input = `w-full ${box}`;
  const can = editable && !busy;

  return (
    <Box className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>Foundation geometry</Label>
        <Tag tone={confirmedNow ? "ok" : "warn"}>
          {confirmedNow ? `Confirmed v${state.version}` : "Proposed: check against the survey markers"}
        </Tag>
      </div>
      <p className="m-0 text-slate-600">
        Every forming quantity is counted from this. Check it once; after that the numbers come out the same every time.
      </p>
      {g.notes && <p className="m-0 rounded bg-orange-50 px-2 py-1 text-orange-900">{g.notes}</p>}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] border-collapse">
          <thead>
            <tr className="border-b border-slate-300 text-left text-xs text-slate-600">
              <th className="py-1 pr-2">Face (outside of wall, in order)</th>
              <th className="w-28 py-1 pr-2">Length ft</th>
              <th className="w-24 py-1">ft-in</th>
              {can && <th className="w-8" />}
            </tr>
          </thead>
          <tbody>
            {g.faces.map((f, i) => (
              <tr key={i} className="border-b border-slate-100">
                <td className="py-1 pr-2"><input value={f.name} disabled={!can} className={input}
                  onChange={(e) => setG({ ...g, faces: g.faces.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} /></td>
                <td className="py-1 pr-2"><input value={f.length_ft} disabled={!can} inputMode="decimal" className={`${input} text-right`}
                  onChange={(e) => setG({ ...g, faces: g.faces.map((x, j) => (j === i ? { ...x, length_ft: Number(e.target.value) } : x)) })} /></td>
                <td className="py-1 text-xs text-slate-600">{ftIn(Number(f.length_ft) || 0)}</td>
                {can && <td><button type="button" className="text-slate-500" onClick={() => setG({ ...g, faces: g.faces.filter((_, j) => j !== i) })}>×</button></td>}
              </tr>
            ))}
            <tr>
              <td className="py-1 font-medium">Outline (outside face of wall)</td>
              <td className="py-1 pr-2 text-right font-medium">{perimeter.toFixed(1)}</td>
              <td className="py-1 text-xs text-slate-600">lin ft</td>
            </tr>
          </tbody>
        </table>
      </div>
      {can && (
        <button type="button" className="text-blue-700" onClick={() => setG({ ...g, faces: [...g.faces, { name: "", length_ft: 0 }] })}>
          Add a face
        </button>
      )}

      <div className="grid gap-2 sm:grid-cols-4">
        {([["outside_corners", "Outside corners"], ["inside_corners", "Inside corners"], ["t_junctions", "T-junctions"],
          ["wall_thickness_in", "Wall thickness, in"], ["wall_height_ft", "Wall height above footing, ft"],
          ["footing_width_in", "Footing width, in"], ["footing_depth_in", "Footing depth, in"],
          ["footing_bars", "Footing bars (continuous)"], ["lap_in", "Rebar lap, in"]] as [keyof Geometry, string][]).map(([k, l]) => (
          <label key={k} className="block text-xs text-slate-600">{l}
            <input value={String(g[k] ?? "")} disabled={!can} inputMode="decimal" onChange={num(k)} className={`mt-0.5 ${input}`} />
          </label>
        ))}
      </div>

      <div className="space-y-1">
        <div className="text-xs font-medium text-slate-700">Interior walls</div>
        {g.interior_walls.map((w, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input value={w.name} disabled={!can} className={`${box} w-64 max-w-full`}
              onChange={(e) => setG({ ...g, interior_walls: g.interior_walls.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
            <input value={w.length_ft} disabled={!can} inputMode="decimal" className={`${box} w-24 text-right`}
              onChange={(e) => setG({ ...g, interior_walls: g.interior_walls.map((x, j) => (j === i ? { ...x, length_ft: Number(e.target.value) } : x)) })} />
            <span className="text-xs text-slate-600">ft</span>
            <label className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={w.footing} disabled={!can}
                onChange={(e) => setG({ ...g, interior_walls: g.interior_walls.map((x, j) => (j === i ? { ...x, footing: e.target.checked } : x)) })} />
              has a footing
            </label>
            {can && <button type="button" className="text-slate-500" onClick={() => setG({ ...g, interior_walls: g.interior_walls.filter((_, j) => j !== i) })}>×</button>}
          </div>
        ))}
        {can && (
          <button type="button" className="text-blue-700"
            onClick={() => setG({ ...g, interior_walls: [...g.interior_walls, { name: "Interior wall", length_ft: 0, footing: true }] })}>
            Add an interior wall
          </button>
        )}
      </div>

      {cornerProblem && <p className="m-0 text-orange-800">{cornerProblem}</p>}
      {err && <p className="m-0 text-red-700">{err}</p>}
      {editable && (
        <div className="flex justify-end">
          <button type="button" disabled={busy || !!cornerProblem || g.faces.length < 4} onClick={confirm}
            className="rounded-md bg-slate-900 px-4 py-2 text-white disabled:opacity-40">
            {confirmedNow ? "Save as a new version" : "This matches the markers: confirm"}
          </button>
        </div>
      )}

      {state.calculated && (
        <div className="space-y-1 border-t border-slate-200 pt-3">
          <div className="text-xs font-medium text-slate-700">
            Calculated from confirmed v{state.confirmed_version}: outline {state.calculated.perimeter_ft} lin ft,
            footing form lines {state.calculated.form_line_lf} lin ft
          </div>
          <ul className="m-0 list-none space-y-0.5 p-0 text-xs text-slate-700">
            {[...state.calculated.lumber, ...state.calculated.concrete].map((r) => (
              <li key={r.calc}><b>{r.qty} {r.unit}</b> {r.item} {r.spec} <span className="text-slate-500">· {r.basis}</span></li>
            ))}
          </ul>
        </div>
      )}
    </Box>
  );
}
