"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { usePortal } from "../portalCtx";
import { getWarranty, type Warranty } from "@/lib/portal";

function fmtDate(s: string | null): string {
  if (!s) return "completion";
  return new Date(s).toLocaleDateString("en-CA", { year: "numeric", month: "long", day: "numeric" });
}

const CARD = "rounded-2xl border bg-white p-5 shadow-sm";
const BORDER = { borderColor: "#ece8e2" };

const MAINTENANCE: { title: string; body: string }[] = [
  { title: "First 30 days", body: "New finishes settle — small drywall cracks or nail pops are normal. Note anything you spot and send it to us; we address it under your workmanship warranty." },
  { title: "Seasonal", body: "Test smoke/CO detectors, clear gutters and drains, and check exterior caulking each season to protect the building envelope." },
  { title: "Keep it dry", body: "Run bath/kitchen fans during and after use, and watch for any moisture around windows or below-grade areas. Report water early." },
];

export default function WarrantyPage() {
  const { project, loading } = usePortal();
  const [w, setW] = useState<Warranty | null>(null);

  useEffect(() => {
    if (project) getWarranty(project.id).then(setW).catch(() => setW(null));
  }, [project]);

  if (loading || !project) return <div className="h-40" />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold" style={{ color: "#1D3146" }}>Warranty &amp; Care</h1>
        <p className="text-sm text-slate-500 mt-1">
          What&rsquo;s covered after we hand over, and how to keep your project in great shape.
        </p>
      </div>

      {/* Workmanship warranty — always */}
      <div className={CARD} style={BORDER}>
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-base font-semibold text-slate-800">1-Year Workmanship Warranty</h2>
          <span className="text-xs text-slate-400 shrink-0">from {fmtDate(w?.projected_completion ?? null)}</span>
        </div>
        <p className="text-sm text-slate-600 mt-2">
          We stand behind our work. For one year after substantial completion, we&rsquo;ll fix any
          workmanship defect at no cost to you. Manufacturer warranties on materials and fixtures
          pass through to you as well.
        </p>
      </div>

      {/* 2-5-10 — dwellings only */}
      {w?.home_warranty_required && (
        <div className={CARD} style={BORDER}>
          <h2 className="text-base font-semibold text-slate-800">2-5-10 Home Warranty</h2>
          <p className="text-sm text-slate-600 mt-2">
            As a new home, your project carries mandatory third-party home warranty insurance
            {w.home_warranty_provider ? <> through <span className="font-medium">{w.home_warranty_provider}</span></> : null},
            in addition to our workmanship warranty:
          </p>
          <ul className="mt-3 space-y-2 text-sm">
            {[
              ["2 years", "labour & materials"],
              ["5 years", "building envelope (water penetration)"],
              ["10 years", "structure of the home"],
            ].map(([y, d]) => (
              <li key={y} className="flex gap-3">
                <span className="font-semibold shrink-0" style={{ color: "#0068A0", width: "4.5rem" }}>{y}</span>
                <span className="text-slate-600">{d}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-slate-400 mt-3">
            Your warranty certificate and full policy will be in your Documents once enrolled.
          </p>
        </div>
      )}

      {/* Report an issue → messaging */}
      <div className="rounded-2xl p-5" style={{ background: "#1D3146" }}>
        <h2 className="text-base font-semibold text-white">Notice something?</h2>
        <p className="text-sm mt-1" style={{ color: "rgba(255,255,255,0.75)" }}>
          Send us the details and a photo — we&rsquo;ll take care of anything covered under your warranty.
        </p>
        <Link
          href="/portal/messages"
          className="inline-block mt-3 rounded-full px-4 py-2 text-sm font-semibold"
          style={{ background: "#0068A0", color: "#fff" }}
        >
          Report an issue
        </Link>
      </div>

      {/* Maintenance tips */}
      <div className="space-y-2">
        <div className="text-[11px] uppercase tracking-wider text-slate-400">Caring for your project</div>
        {MAINTENANCE.map((m) => (
          <div key={m.title} className="rounded-xl border bg-white px-4 py-3 shadow-sm" style={BORDER}>
            <div className="text-sm font-medium text-slate-800">{m.title}</div>
            <div className="text-sm text-slate-600 mt-0.5">{m.body}</div>
          </div>
        ))}
      </div>

      <Link href="/portal/documents" className="block text-center text-sm" style={{ color: "#0068A0" }}>
        View your documents →
      </Link>
    </div>
  );
}
