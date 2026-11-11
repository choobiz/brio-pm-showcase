/** Shown when a fetch failed (see lib/loadOr). Deliberately carries no error
 *  text: a sentence and a next step, never the server's response body. */
export function LoadFailed({ what, small = false }: { what: string; small?: boolean }) {
  if (small) {
    return <p className="text-xs text-amber-800">Couldn&apos;t load {what}. Reload in a few seconds.</p>;
  }
  return (
    <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="alert">
      <strong>Couldn&apos;t load {what}.</strong> This is a loading failure, not missing data, so don&apos;t read it
      as nothing being there. Reload in a few seconds; if it keeps happening the server may be restarting after a deploy.
    </div>
  );
}
