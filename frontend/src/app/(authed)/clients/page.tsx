"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { listClients, type ClientListItem } from "@/lib/clients";

export default function ClientsPage() {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<ClientListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const handle = setTimeout(() => {
      listClients(q)
        .then((r) => {
          if (!cancelled) setRows(r);
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 200); // light debounce on the search input
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [q]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Clients
        </h1>
        <Link href="/clients/new">
          <Button variant="primary">New client</Button>
        </Link>
      </div>

      <div className="max-w-md">
        <Input
          placeholder="Search by name, email, or phone..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="search"
        />
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="bg-white rounded-md shadow border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500 border-b border-slate-200">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Phone</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Projects</th>
              <th className="px-4 py-3">Last activity</th>
              <th className="px-4 py-3">Tags</th>
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-slate-400">
                  Loading...
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-slate-400">
                  No clients yet.
                </td>
              </tr>
            ) : (
              rows.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/clients/${c.id}`}
                      className="font-medium"
                      style={{ color: "var(--color-brio-primary)" }}
                    >
                      {c.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-700">{c.primary_phone ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-700">{c.primary_email ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-700">{c.project_count}</td>
                  <td className="px-4 py-3 text-slate-700">{c.last_activity ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-500">
                    {c.tags.length ? c.tags.join(", ") : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
