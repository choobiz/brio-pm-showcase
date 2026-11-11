import { listContracts, getContract, fmtDate, fmtMoney, type ContractStatus } from "@/lib/workbench";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";
import { StaleBanner } from "../StaleBanner";
import { ContractStatusControls } from "./ContractStatusControls";
import { GenerateContractButton } from "./GenerateContractButton";
import { SignatoriesEditor } from "./SignatoriesEditor";
import { PaymentTermsEditor } from "./PaymentTermsEditor";
import { UpdateFromEstimateBanner } from "./UpdateFromEstimateBanner";
import { BillingPlan } from "./BillingPlan";
import { ContractChecks, LateBanner } from "./ContractChecks";
import type { Signatory } from "@/lib/workbench";

/** Pull the signatory list out of the contract payload (JSONB), defensively —
 *  it's an optional array of {name, title}. */
function readSignatories(payload: Record<string, unknown> | null | undefined): Signatory[] {
  const raw = (payload as { signatories?: unknown })?.signatories;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((s): s is Record<string, unknown> => typeof s === "object" && s !== null)
    .map((s) => ({
      name: typeof s.name === "string" ? s.name : "",
      title: typeof s.title === "string" ? s.title : "",
    }));
}

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-slate-200 text-slate-800",
  issued: "bg-blue-100 text-blue-800",
  signed: "bg-emerald-100 text-emerald-800",
  executed: "bg-emerald-200 text-emerald-900",
};

export default async function ContractPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  // A failed load must not show "No contract yet" + Generate: that invites a
  // duplicate contract during an outage.
  const res = await loadOr("contracts", listContracts(id));
  if (res.failed) return <LoadFailed what="the contract" />;
  const list = res.data ?? [];
  const latest = list[list.length - 1];

  if (!latest) {
    return (
      <div className="rounded border border-slate-200 bg-white p-6 space-y-3">
        <h1 className="text-lg font-semibold">No contract yet</h1>
        <p className="text-sm text-slate-600 max-w-lg">
          Generate a contract record from this project&rsquo;s active estimate and
          schedule. It captures the contract value, key dates, and payment
          schedule, and unlocks the status workflow (issued → signed → executed)
          plus the downloadable contract PDF.
        </p>
        <GenerateContractButton projectId={id} />
      </div>
    );
  }

  const c = await getContract(id, latest.id);
  // numeric() columns come back as strings from FastAPI (Decimal preserves
  // precision). Coerce to Number explicitly so `+` does numeric addition,
  // not string concatenation (which would cascade to NaN through fmtMoney).
  const baseVal = Number(c.base_contract_value ?? 0);
  const optVal = Number(c.option_contract_value ?? 0);
  // Approved change orders (pre-tax, no markup) fold into the revised value so the
  // page matches the contract PDF + portal. baseVal stays raw for the stale banner.
  const coPretax = Number(c.approved_co_pretax ?? 0);
  const revisedPretax = baseVal + coPretax;
  const baseGst = revisedPretax * 0.05;
  const optGst = optVal * 0.05;
  const baseTotal = revisedPretax + baseGst;
  const optTotal = optVal + optGst;
  const combined = baseTotal + optTotal;

  const card = "rounded border border-slate-200 bg-white p-4";

  return (
    <div className="space-y-5">
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">{c.reference}</h1>
          <p className="text-sm text-slate-600 mt-1">
            Template: {c.template_type} · v{c.version_number}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <a
            href={`/api/workbench/client-package-pdf/${id}?doc=contract`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-brio-primary)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
            title="Download the contract as a standalone PDF to send to the client"
          >
            ⬇ Download contract PDF
          </a>
          <span
            className={`inline-block px-3 py-1 rounded text-xs font-bold uppercase ${
              STATUS_COLORS[c.status] || "bg-slate-200 text-slate-800"
            }`}
          >
            {c.status}
          </span>
        </div>
      </header>

      <LateBanner projectId={id} />

      <StaleBanner
        projectId={id}
        basedOnEstimateId={c.based_on_estimate_id}
        isStale={c.is_stale}
      />

      {c.value_stale && c.active_base_contract_value != null && (
        <UpdateFromEstimateBanner
          projectId={id}
          storedInclGst={(baseVal + optVal) * 1.05}
          activeInclGst={
            (Number(c.active_base_contract_value) + Number(c.active_option_contract_value ?? 0)) * 1.05
          }
          locked={c.status === "signed" || c.status === "executed"}
        />
      )}

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Parties
        </h2>
        <table className="w-full text-sm">
          <tbody>
            <tr>
              <td className="py-1 text-slate-600 w-32">Owner</td>
              <td className="py-1">
                {c.owner_name || <span className="text-slate-400 italic">[OWNER LEGAL NAME]</span>}
              </td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">Owner address</td>
              <td className="py-1">
                {c.owner_address || (
                  <span className="text-slate-400 italic">[OWNER ADDRESS]</span>
                )}
              </td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">Contractor</td>
              <td className="py-1">BRIO Construction Inc.</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Contract Value
        </h2>
        <table className="w-full text-sm">
          <tbody>
            <tr>
              <td className="py-1">Base Contract Value (pre-tax)</td>
              <td className="py-1 text-right">{fmtMoney(baseVal)}</td>
            </tr>
            {coPretax !== 0 && (
              <>
                <tr>
                  <td className="py-1 text-slate-600">Approved change orders (pre-tax)</td>
                  <td className="py-1 text-right text-slate-600">
                    {coPretax > 0 ? "+" : "−"}{fmtMoney(Math.abs(coPretax))}
                  </td>
                </tr>
                <tr className="border-t border-slate-200">
                  <td className="py-1">Revised Contract Value (pre-tax)</td>
                  <td className="py-1 text-right">{fmtMoney(revisedPretax)}</td>
                </tr>
              </>
            )}
            <tr>
              <td className="py-1 text-slate-600">GST 5%</td>
              <td className="py-1 text-right text-slate-600">{fmtMoney(baseGst)}</td>
            </tr>
            <tr className="border-t border-slate-300 font-medium">
              <td className="py-1">{coPretax !== 0 ? "Contract Total (incl. GST)" : "Base Total (incl. GST)"}</td>
              <td className="py-1 text-right">{fmtMoney(baseTotal)}</td>
            </tr>
            {optVal > 0 && (
              <>
                <tr>
                  <td className="py-1 pt-3">Optional Add-On (Trade 16 — Site Work)</td>
                  <td className="py-1 text-right pt-3">{fmtMoney(optVal)}</td>
                </tr>
                <tr>
                  <td className="py-1 text-slate-600">GST 5%</td>
                  <td className="py-1 text-right text-slate-600">{fmtMoney(optGst)}</td>
                </tr>
                <tr className="border-t border-amber-200 font-medium text-amber-700">
                  <td className="py-1">Option Total (incl. GST)</td>
                  <td className="py-1 text-right">{fmtMoney(optTotal)}</td>
                </tr>
                <tr className="border-t border-slate-400 font-semibold">
                  <td className="py-2">BASE + OPTION COMBINED (incl. GST)</td>
                  <td className="py-2 text-right">{fmtMoney(combined)}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </section>

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Key Dates
        </h2>
        <table className="w-full text-sm">
          <tbody>
            <tr>
              <td className="py-1 text-slate-600 w-48">Project start</td>
              <td className="py-1">{fmtDate(c.project_start_date)}</td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">Substantial completion</td>
              <td className="py-1">{fmtDate(c.substantial_completion_date)}</td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">Final completion</td>
              <td className="py-1">{fmtDate(c.final_completion_date)}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-3">
          Status
        </h2>
        <ContractStatusControls
          projectId={id}
          contractId={c.id}
          status={c.status as ContractStatus}
          ownerNameDefault={c.owner_name ?? ""}
        />
        {c.signed_at && (
          <div className="mt-3 border-t border-slate-100 pt-3 text-sm text-slate-600">
            Signed on {fmtDate(c.signed_at)}
            {c.signed_by_owner && <> by {c.signed_by_owner}</>}
            {c.signed_by_contractor && (
              <> · Contractor: {c.signed_by_contractor}</>
            )}
          </div>
        )}
      </section>

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-3">
          Signatories
        </h2>
        <SignatoriesEditor
          projectId={id}
          contractId={c.id}
          initial={readSignatories(c.payload)}
          ownerNameDefault={c.owner_name ?? ""}
        />
      </section>

      <PaymentTermsEditor projectId={id} />

      <BillingPlan projectId={id} />

      <ContractChecks projectId={id} />

      <p className="text-xs text-slate-500">
        Use “Download contract PDF” above to send the contract as a standalone
        file. In-app signature capture is coming later; for now, have the owner
        sign the downloaded PDF.
      </p>
    </div>
  );
}
