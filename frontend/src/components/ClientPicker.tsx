"use client";

import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { findOrCreateClient, listClients, type ClientListItem } from "@/lib/clients";

/**
 * Autocomplete combobox over the client registry. Calls
 * ``GET /clients?q=`` while the user types and offers a "create new" row at
 * the bottom when no match exists.
 *
 * Track B (Projects CRUD) reuses this on the project create form so we
 * don't accidentally fork an existing client into a duplicate row.
 */
export interface ClientPickerProps {
  /** Called when the user picks a row or successfully creates a new client. */
  onPick: (client: { id: string; name: string }) => void;
  /** Initial display text (e.g. when editing). */
  initialQuery?: string;
  /** Show the inline "create new client with this name" row. */
  allowCreate?: boolean;
  placeholder?: string;
}

export function ClientPicker({
  onPick,
  initialQuery = "",
  allowCreate = true,
  placeholder = "Search clients...",
}: ClientPickerProps) {
  const [q, setQ] = useState(initialQuery);
  const [rows, setRows] = useState<ClientListItem[]>([]);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!q.trim()) {
      setRows([]);
      return;
    }
    const handle = setTimeout(() => {
      listClients(q)
        .then((r) => {
          if (!cancelled) setRows(r);
        })
        .catch(() => {
          if (!cancelled) setRows([]);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [q]);

  // Close dropdown on outside click.
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  async function createInline() {
    if (!q.trim() || creating) return;
    setCreating(true);
    try {
      const res = await findOrCreateClient({ name: q.trim() });
      onPick({ id: res.client.id, name: res.client.name });
      setQ(res.client.name);
      setOpen(false);
    } finally {
      setCreating(false);
    }
  }

  const trimmed = q.trim();
  const exactMatch = rows.find((r) => r.name.toLowerCase() === trimmed.toLowerCase());

  return (
    <div ref={containerRef} className="relative">
      <Input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
      />
      {open && (rows.length > 0 || (allowCreate && trimmed && !exactMatch)) && (
        <div className="absolute z-10 mt-1 w-full max-h-64 overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {rows.map((r) => (
            <button
              key={r.id}
              type="button"
              className="block w-full text-left px-3 py-2 text-sm hover:bg-slate-50 border-b border-slate-100"
              onClick={() => {
                onPick({ id: r.id, name: r.name });
                setQ(r.name);
                setOpen(false);
              }}
            >
              <div className="font-medium">{r.name}</div>
              <div className="text-xs text-slate-500">
                {r.primary_phone ?? r.primary_email ?? "—"} · {r.project_count} project
                {r.project_count === 1 ? "" : "s"}
              </div>
            </button>
          ))}
          {allowCreate && trimmed && !exactMatch && (
            <button
              type="button"
              disabled={creating}
              onClick={createInline}
              className="block w-full text-left px-3 py-2 text-sm hover:bg-slate-50 text-[var(--color-brio-primary)]"
            >
              {creating ? "Creating..." : `+ Create new client "${trimmed}"`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
