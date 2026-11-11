import { getEstimateVersion } from "@/lib/workbench";
import { EstimatePayload } from "@/lib/estimate-rollup";
import { EstimateEditor } from "./EstimateEditor";

/**
 * Edit page for one estimate version. Server-side fetch of the version
 * detail (which includes the full JSONB payload), then handoff to the
 * client editor for the actual mutation UX.
 *
 * Save creates a new immutable version v(max+1) on this project, marks
 * it active, and redirects to its read-only detail page.
 */
export default async function EditEstimatePage({
  params,
}: {
  params: Promise<{ id: string; vid: string }>;
}) {
  const { id, vid } = await params;
  const v = await getEstimateVersion(id, vid);

  // payload is JSONB on the server; cast to our editor's payload type
  const payload = v.payload as unknown as EstimatePayload;

  return (
    <EstimateEditor
      projectId={id}
      parentVersionId={vid}
      initialPayload={{
        ...payload,
        // Make sure overhead/profit and reference are surfaced even if the
        // payload structure leaves them at the top-level v.* fields only.
        overhead: payload.overhead ?? Number(v.overhead_pct) ?? 0,
        profit: payload.profit ?? Number(v.profit_pct) ?? 0,
        reference: payload.reference ?? v.reference,
        notes: payload.notes ?? v.notes ?? "",
      }}
    />
  );
}
