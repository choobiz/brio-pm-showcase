// No imports: scripts/check-estimate-email.mjs loads this file directly in Node.

export type EmailOption = {
  id: string;
  version_number: number;
  reference: string;
  is_active: boolean;
  title: string;
  scope_reference?: string | null;
  subtotal: number;
  total: number;
};

/** Which estimates start ticked: the one being viewed, plus the newest version
 * of every other scope's estimate (456: corridor v008 + suite v007), so a
 * multi-scope project's packages go out together. */
export function defaultPicks(options: EmailOption[], viewing: string): string[] {
  const picks = new Set<string>([viewing]);
  const viewed = options.find((o) => o.id === viewing);
  const seen = new Set<string>([viewed?.scope_reference ?? `__${viewing}`]);
  const newestFirst = [...options].sort((a, b) => b.version_number - a.version_number);
  for (const o of newestFirst) {
    if (!o.scope_reference || seen.has(o.scope_reference)) continue;
    seen.add(o.scope_reference);
    picks.add(o.id);
  }
  return [...picks];
}

/** A mailto: link the user's mail app opens with the draft filled in. */
export function mailtoHref(subject: string, body: string, to?: string | null): string {
  return `mailto:${encodeURIComponent(to ?? "")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
