import { api } from "./api";

// ── Types ────────────────────────────────────────────────────────────────
export type EstimateProvenance = {
  takeoff_mode?: "cad" | "pdf" | "none";
  confidence?: "measured" | "partially_measured" | "estimated" | "insufficient";
  method?: "cad" | "scope";
  source?: "cad_measured" | "rate_scope";
  fell_back?: boolean;
  needs_review?: boolean;
  reconciliation?: string;
};

export type EstimateVersionSummary = {
  id: string;
  version_number: number;
  reference: string;
  label: string | null;
  cost_basis: string;
  overhead_pct: number;
  profit_pct: number;
  parent_version_id: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  created_by_id: string | null;
  provenance: EstimateProvenance | null;
  /** The job that wrote it, when the AI did. */
  ai_job_id?: string | null;
  /** Set when this version was made by describing a change to another. */
  revision?: { from_version: number | null; instruction: string } | null;
  /** Server-computed, the same arithmetic Home and the portal use. */
  totals?: EstimateTotals;
};

export type EstimateTotals = {
  subtotal: number;
  overhead_pct: number;
  overhead: number;
  profit_pct: number;
  profit: number;
  before_gst: number;
  gst: number;
  with_gst: number;
  optional_subtotal: number;
  detail_level: string | null;
};

export type EstimateVersionDetail = EstimateVersionSummary & {
  project_id: string;
  payload: Record<string, unknown>;
};

// Phase 3a — workbench landing-page context.
export type WorkbenchOverview = {
  active_estimate: {
    id: string;
    version_number: number;
    reference: string;
    label: string | null;
    overhead_pct: number;
    profit_pct: number;
  } | null;
  counts: {
    estimates: number;
    schedules: number;
    cash_flows: number;
    sovs: number;
    contracts: number;
    narratives: number;
  };
};

export type ActivityRow = {
  id: string;
  activity_code: string;
  name: string;
  phase: string;
  predecessor_codes: string[];
  lag_wd: number;
  optimistic_wd: number | null;
  most_likely_wd: number | null;
  pessimistic_wd: number | null;
  expected_wd: number | null;
  variance: number | null;
  early_start_wd: number | null;
  early_finish_wd: number | null;
  late_start_wd: number | null;
  late_finish_wd: number | null;
  total_float_wd: number | null;
  free_float_wd: number | null;
  start_date: string | null;
  finish_date: string | null;
  is_critical: boolean;
  is_milestone: boolean;
};

export type InspectionRow = {
  id: string;
  activity_id: string | null;
  authority: string;
  inspection_type: string;
  lead_time_days: number | null;
  gates: string | null;
  notes: string | null;
};

export type ScheduleSummary = {
  id: string;
  version_number: number;
  project_start: string;
  working_hours: string;
  holidays: string[];
  deterministic_finish_date: string | null;
  p50_finish_date: string | null;
  p80_finish_date: string | null;
  project_expected_wd: number | null;
  project_std_dev_wd: number | null;
  critical_path_activity_codes: string[];
  notes: string | null;
  created_at: string;
  /** The version "compare with plan" measures against (one per project). */
  is_baseline?: boolean;
  client_visible?: boolean;
};

export type ScheduleDetail = ScheduleSummary & {
  activities: ActivityRow[];
  inspections: InspectionRow[];
};

export type CashFlowSummary = {
  id: string;
  version_number: number;
  scenario: "base" | "combined";
  total_pretax: number;
  final_cash_position: number | null;
  min_cash_position: number | null;
  min_position_week_end: string | null;
  bridge_financing_required: number | null;
  owner_net_days: number;
  lien_holdback_pct: number;
  lien_release_days_post_substantial: number;
  deficiency_days_post_occupancy: number;
  based_on_estimate_id: string | null;
  is_stale: boolean;
  created_at: string;
};

export type CashFlowWeek = {
  week_num: number;
  week_start: string;
  week_end: string;
  draws: { milestone: string; amount: number; gross?: number }[];
  draws_total: number;
  outflow_total: number;
  net_this_week: number;
  cumulative_position: number;
};

export type CashFlowDetail = CashFlowSummary & {
  weekly_periods: CashFlowWeek[];
  phase_costs: Record<string, number>;
  milestone_dates: Record<string, string>;
  key_dates: Record<string, string>;
  based_on_estimate_id: string | null;
  based_on_schedule_id: string | null;
  is_stale: boolean;
  notes: string | null;
};

export type SovSummary = {
  id: string;
  version_number: number;
  scenario: "base" | "combined";
  total_pretax: number;
  based_on_estimate_id: string | null;
  is_stale: boolean;
  notes: string | null;
  created_at: string;
};

export type SovRow = {
  tradeNumber: string;
  description: string;
  optional: boolean;
  scheduledValue: number;
  monthly: Record<string, number>;
  cumulativePct: Record<string, number>;
};

export type SovDetail = SovSummary & {
  months: string[];
  rows: SovRow[];
  monthly_totals: Record<string, number>;
  cumulative_totals: Record<string, number>;
  lien_holdback_10pct: Record<string, number>;
  owner_net_monthly: Record<string, number>;
};

export type ContractSummary = {
  id: string;
  version_number: number;
  reference: string;
  template_type: string;
  status: "draft" | "issued" | "signed" | "executed";
  owner_name: string | null;
  base_contract_value: number | null;
  option_contract_value: number | null;
  based_on_estimate_id: string | null;
  is_stale: boolean;
  project_start_date: string | null;
  substantial_completion_date: string | null;
  final_completion_date: string | null;
  signed_at: string | null;
  created_at: string;
};

export type ContractDetail = ContractSummary & {
  owner_address: string | null;
  based_on_schedule_id: string | null;
  based_on_sov_id: string | null;
  payload: Record<string, unknown>;
  signed_by_owner: string | null;
  signed_by_contractor: string | null;
  notes: string | null;
  // Value-based staleness: true when the stored contract value no longer
  // matches what the ACTIVE estimate produces (catches in-place estimate edits
  // that the id-based is_stale misses).
  value_stale: boolean;
  active_base_contract_value: number | null;
  active_option_contract_value: number | null;
  // Sum of approved change orders (pre-tax, no markup). Folds into the revised
  // contract value shown on the page, matching the contract PDF + client portal.
  approved_co_pretax: number | null;
};

export type NarrativeSummary = {
  id: string;
  parent_table: string;
  parent_id: string | null;
  title: string;
  version_number: number;
  created_at: string;
  content_size: number;
  /** True when a later version chains back to this row. */
  is_superseded: boolean;
};

export type NarrativeDetail = NarrativeSummary & {
  content_md: string;
  /** `doc:<uuid>` image refs in `content_md`, resolved to presigned R2 URLs at
   *  read time. Minted per request — never store one. */
  images: Record<string, string>;
  /** The version that replaced this one, when there is one. */
  superseded_by: { id: string; version_number: number } | null;
};

export type AuditLogRow = {
  id: string;
  table_name: string;
  row_id: string;
  action: string;
  diff: Record<string, unknown> | null;
  user_id: string | null;
  created_at: string;
};

// ── Fetch helpers ────────────────────────────────────────────────────────
const base = (pid: string) => `/projects/${pid}/workbench`;

export type ReadinessChecklistItem = {
  id: string;
  label: string;
  unlocks: string;
  critical: boolean;
  present: boolean;
};
export type EstimateReadiness = {
  takeoff_mode: "cad" | "pdf" | "none";
  confidence: "measured" | "partially_measured" | "estimated" | "insufficient";
  message: string;
  present: { cad: boolean; pdf_drawings: boolean; scope: boolean; photos: boolean };
  checklist: ReadinessChecklistItem[];
  disciplines: Record<string, { cad: boolean; pdf: boolean }>;
  /** Discipline-aware grade. `confidence` is derived from this, not from CAD presence. */
  accuracy: {
    grade: "measured_complete" | "measured_partial" | "read_from_pdf" | "scope_only";
    headline: string;
    coverage_pct: number;
    allowance_divisions: string[];
    measured_divisions: string[];
    next_uploads: { label?: string; why?: string }[];
    warnings: string[];
  };
  counts: { cad: number; pdf_drawings: number };
  missing_critical: string[];
  /** Which trades to run. estimate_full costs one AI call per discipline. */
  discipline_plan?: {
    recommended: string[];
    skipped: string[];
    reasons: Record<string, string[]>;
    basis: "scope" | "none";
  };
};

export const getEstimateReadiness = (pid: string) =>
  api<EstimateReadiness>(`${base(pid)}/estimate-readiness`);

export const getWorkbenchOverview = (pid: string) =>
  api<WorkbenchOverview>(`${base(pid)}/overview`);

export const listEstimateVersions = (pid: string) =>
  api<EstimateVersionSummary[]>(`${base(pid)}/estimate-versions`);

export const getEstimateVersion = (pid: string, vid: string) =>
  api<EstimateVersionDetail>(`${base(pid)}/estimate-versions/${vid}`);

export const activateEstimateVersion = (pid: string, vid: string) =>
  api<{ id: string; version_number: number; reference: string; is_active: true }>(
    `${base(pid)}/estimate-versions/${vid}/activate`,
    { method: "POST" }
  );

// ── Contract lifecycle ──────────────────────────────────────────────────────
export type ContractStatus = "draft" | "issued" | "signed" | "executed";

export type Signatory = { name: string; title: string };

/** Replace the contract's signatory list (rendered into the PDF signature block). */
export const setContractSignatories = (
  pid: string,
  contractId: string,
  signatories: Signatory[],
) =>
  api<{ id: string; signatories: Signatory[] }>(
    `${base(pid)}/contracts/${contractId}/signatories`,
    { method: "PUT", body: JSON.stringify({ signatories }) },
  );

/** Create a contract record from the project's active estimate + schedule. */
export const createContract = (pid: string) =>
  api<{ id: string; reference: string; version_number: number; status: ContractStatus }>(
    `${base(pid)}/contracts`,
    { method: "POST", body: JSON.stringify({}) },
  );

export const setContractStatus = (
  pid: string,
  contractId: string,
  body: {
    status: ContractStatus;
    signed_by_owner?: string;
    signed_by_contractor?: string;
    signed_date?: string; // ISO 'YYYY-MM-DD'
  },
) =>
  api<{ id: string; status: ContractStatus }>(
    `${base(pid)}/contracts/${contractId}/status`,
    { method: "POST", body: JSON.stringify(body) },
  );

// ── Activation regeneration (Phase B/C) ─────────────────────────────────────
export interface RegenerationPlan {
  artifacts: { name: string; kind: "ai" | "deterministic"; cost: number }[];
  total_cost_usd: number;
}
export interface RegenerationResult {
  approval_id: string;
  activated_estimate_id: string;
  jobs: { kind: string; job_id: string }[];
  estimated_cost_usd: number;
}

export const getRegenerationPlan = (pid: string, vid: string) =>
  api<RegenerationPlan>(`${base(pid)}/estimate-versions/${vid}/regeneration-plan`);

export const activateWithRegeneration = (pid: string, vid: string) =>
  api<RegenerationResult>(
    `${base(pid)}/estimate-versions/${vid}/activate-with-regeneration`,
    { method: "POST" },
  );


// ── Prior client estimate (persisted for the deck's "why higher" section) ────
export interface PriorClientEstimate {
  total: number;
  date?: string | null;
  note?: string | null;
}
export const getPriorClientEstimate = (pid: string) =>
  api<{ prior_client_estimate: PriorClientEstimate | null }>(`${base(pid)}/prior-client-estimate`);
export const setPriorClientEstimate = (pid: string, v: PriorClientEstimate | null) =>
  api<{ prior_client_estimate: PriorClientEstimate | null }>(
    `${base(pid)}/prior-client-estimate`,
    { method: "PUT", body: JSON.stringify({ prior_client_estimate: v }) },
  );


// ── Presentations (client deck) ─────────────────────────────────────────────
export interface PresentationSummary {
  id: string;
  audience: string;
  version_number: number;
  label: string | null;
  status: string;
  based_on_estimate_id: string | null;
  share_token: string | null;
  ai_job_id: string | null;
  created_at: string;
}

// The deck payload assembled by the presentation worker (numbers verbatim +
// AI prose). Typed loosely for the fields the viewer renders.
export interface PresentationPayload {
  generatedAt?: string;
  project?: { name?: string; address?: string; type?: string; sqft?: number };
  client?: { name?: string; company?: string } | null;
  headline?: string;
  overview?: string;
  scopeSections?: { title: string; discipline?: string; clientDescription: string; featureDrawingDisciplines?: string[] }[];
  budget?: {
    subtotal: number; allowances: number; gst: number; total: number;
    reference?: string | null; detailLevel?: string | null; validDays?: number | null;
    categories: { name: string; discipline?: string | null; description?: string; displayTotal: number; optional?: boolean; itemCount?: number; includes?: string[] }[];
    paymentSchedule?: { label?: string; milestone?: string; percent?: number; amount?: number }[];
  };
  estimateChange?: { priorTotal: number; priorDate?: string | null; priorNote?: string | null; currentTotal: number; delta: number; explanation?: string | null } | null;
  highCostItems?: { categoryName: string; displayTotal: number; note: string | null }[];
  schedule?: {
    present: boolean; projectStart?: string | null; finishP50?: string | null; finishP80?: string | null;
    finishDeterministic?: string | null; narrative?: string | null;
    critical?: { code?: string; name?: string; start?: string | null; finish?: string | null }[];
    milestones?: { code?: string; name?: string; date?: string | null }[];
    activities?: { code?: string; name?: string; phase?: string; duration?: number; start?: string | null; finish?: string | null; critical?: boolean; milestone?: boolean }[];
  };
  cashFlow?: { present: boolean; bridgeFinancing?: number; minCashPosition?: number; finalCashPosition?: number; narrative?: string | null };
  ownerDecisions?: { title: string; whatToDecide: string; affectsSchedule?: string | null; leadTimeWeeks?: number | null; decideByDate?: string | null }[];
  ourAssumptions?: { title: string; assumption: string; ifWrong: string }[];
  drawings?: { id: string; filename: string; discipline: string }[];
  assumptions?: string[];
  exclusions?: string[];
  closingNote?: string | null;
  provenance?: { estimateId?: string; scheduleId?: string | null; cashFlowId?: string | null; stale?: string[] };
}

export interface PresentationDetail extends PresentationSummary {
  project_id: string;
  payload: PresentationPayload;
  based_on_schedule_id: string | null;
  based_on_cash_flow_id: string | null;
  pdf_r2_key: string | null;
}

export const listPresentations = (pid: string) =>
  api<PresentationSummary[]>(`${base(pid)}/presentations`);

export const getPresentation = (pid: string, prid: string) =>
  api<PresentationDetail>(`${base(pid)}/presentations/${prid}`);

export const sharePresentation = (pid: string, prid: string) =>
  api<{ share_token: string; url: string }>(
    `${base(pid)}/presentations/${prid}/share`, { method: "POST" });

// Phase 3b — create a new immutable estimate version from an edited payload.
// `body` carries the fully-recomputed payload + parent_version_id; backend
// assigns the next version_number and clears the prior is_active row.
export const createEstimateVersion = (
  pid: string,
  body: {
    parent_version_id?: string;
    payload: Record<string, unknown>;
    notes?: string;
    overhead_pct?: number;
    profit_pct?: number;
    reference?: string;
    label?: string;
    rate_updates?: { key: string; write: boolean; reason?: string }[];
  },
) =>
  api<{ id: string; version_number: number; reference: string; is_active: true }>(
    `${base(pid)}/estimate-versions`,
    { method: "POST", body: JSON.stringify(body) },
  );

// Phase 3b — update an existing estimate version in place. Use when the
// edit is small and you don't want a new version_number in the audit
// trail. Same payload shape as the create endpoint.
export const updateEstimateVersion = (
  pid: string,
  vid: string,
  body: {
    payload: Record<string, unknown>;
    notes?: string;
    overhead_pct?: number;
    profit_pct?: number;
    reference?: string;
    rate_updates?: { key: string; write: boolean; reason?: string }[];
  },
) =>
  api<{ id: string; version_number: number; reference: string }>(
    `${base(pid)}/estimate-versions/${vid}`,
    { method: "PUT", body: JSON.stringify(body) },
  );

// Phase 3a Step 5 — regenerate downstream artifacts (Contract / SOV / CF)
// against the project's currently-active estimate. See backend doc for
// per-artifact recompute semantics.
export const regenerateDownstream = (pid: string) =>
  api<{
    active_estimate_id: string;
    contract: { id: string; base_contract_value: number; option_contract_value: number } | null;
    sovs: { id: string; scenario: string; total_pretax: number }[];
    cash_flows: { id: string; scenario: string; status: string }[];
  }>(`${base(pid)}/regenerate-downstream`, { method: "POST" });

// Phase 4 Step 2 — persist an edited schedule as a new version.
export const createScheduleVersion = (pid: string, body: Record<string, unknown>) =>
  api<{ id: string; version_number: number; critical_path_activity_codes: string[] }>(
    `${base(pid)}/schedules`,
    { method: "POST", body: JSON.stringify(body) },
  );

// Phase 6 — drawing-link CRUD for an estimate version
export type DrawingLink = {
  id: string;
  trade_name: string;
  line_item_index: number;
  document_id: string;
  sheet_number: string | null;
  tag_reference: string | null;
  page_number: number | null;
  verified: boolean;
  notes: string | null;
  created_at: string;
  filename: string;
  category: string;
};
export type ProjectDrawing = {
  id: string;
  filename: string;
  category: string;
  phase: string | null;
  mime: string;
  size_bytes: number;
  created_at: string;
};
export const listDrawingLinks = (pid: string, vid: string) =>
  api<DrawingLink[]>(`${base(pid)}/estimate-versions/${vid}/drawing-links`);
export const listProjectDrawings = (pid: string) =>
  api<ProjectDrawing[]>(`${base(pid)}/drawings`);
export const createDrawingLink = (
  pid: string,
  vid: string,
  body: {
    trade_name: string;
    line_item_index: number;
    document_id: string;
    sheet_number?: string;
    tag_reference?: string;
    page_number?: number;
    notes?: string;
    verified?: boolean;
  },
) =>
  api<{ id: string }>(`${base(pid)}/estimate-versions/${vid}/drawing-links`, {
    method: "POST",
    body: JSON.stringify(body),
  });
export const deleteDrawingLink = (pid: string, linkId: string) =>
  api<void>(`${base(pid)}/drawing-links/${linkId}`, { method: "DELETE" });

export const listSchedules = (pid: string) =>
  api<ScheduleSummary[]>(`${base(pid)}/schedules`);

export const getSchedule = (pid: string, sid: string) =>
  api<ScheduleDetail>(`${base(pid)}/schedules/${sid}`);

/** What actually happened per activity: site reports (drafts flagged) with
 *  typed-in facts on top. Keyed by activity_code, not schedule version. */
export type ActivityActual = {
  activity_code: string;
  status: "done" | "started" | "not_started" | null;
  actual_start: string | null;
  actual_finish: string | null;
  percent_complete: number | null;
  reason: string | null;
  last_reported: string | null;
  from_draft: boolean;
  source: "site_report" | "typed" | "both";
};

/** Bookings (Schedule › Bookings & letters) for server pages. lib/milestones
 *  is the client-side twin; it fetches a relative URL and cannot run here. */
export type BookingItem = {
  id: string;
  title: string;
  status: string;
  activity_code: string | null;
  book_by: string | null;
  need_by: string | null;
  booked_for: string | null;
  late: boolean;
};
export const listBookings = (pid: string) =>
  api<{ items: BookingItem[] }>(`/projects/${encodeURIComponent(pid)}/milestones`);

export const getActuals = (pid: string) =>
  api<{ actuals: ActivityActual[] }>(`/projects/${pid}/schedule/actuals`);

export type ReplanChange = {
  activity_code: string;
  name: string | null;
  state: "done" | "assumed" | "started" | "kept" | "kept_started" | "planned";
  old_start: string | null;
  old_finish: string | null;
  new_start: string;
  new_finish: string;
  finish_moved_days: number | null;
  moved: boolean;
  is_critical: boolean;
  was_critical: boolean;
};

export type ReplanPreview = {
  base: { id: string; version_number: number; p50_finish_date: string; p80_finish_date: string; client_visible: boolean };
  client_p50_finish_date: string | null;
  data_date: string;
  unanswered_rule: "keep" | "push";
  p50_finish_date: string;
  p80_finish_date: string;
  other_rule: { unanswered_rule: "keep" | "push"; p50_finish_date: string; p80_finish_date: string };
  changes: ReplanChange[];
  unchanged: number;
  assumed: { activity_code: string; name: string | null }[];
  kept: { activity_code: string; name: string | null }[];
  critical_path_activity_codes: string[];
};

/** Schedule › Update the plan: what a re-plan from the records would give. Writes nothing. */
export const getReplanPreview = (pid: string, unanswered: "keep" | "push") =>
  api<ReplanPreview>(`/projects/${pid}/schedule/replan?unanswered=${unanswered}`);

export const listCashFlows = (pid: string) =>
  api<CashFlowSummary[]>(`${base(pid)}/cash-flows`);

export const getCashFlow = (pid: string, cid: string) =>
  api<CashFlowDetail>(`${base(pid)}/cash-flows/${cid}`);

export const listSovs = (pid: string) =>
  api<SovSummary[]>(`${base(pid)}/sovs`);

export const getSov = (pid: string, sid: string) =>
  api<SovDetail>(`${base(pid)}/sovs/${sid}`);

// R2Billing (2026-11-02): SOV with % done / earned / billed / left, and cash by month.
export type SovProgress = {
  as_of: string;
  sov: { id: string; version_number: number };
  rows: { line: string; description: string; division: string | null; value: number; pct_done: number | null;
          earned: number | null; billed: number; left: number }[];
  total: { value: number; earned: number; pct_done: number; billed: number; left: number; billed_ahead: number };
  pct_note: string | null;
  months: { month: string; in: number; out: number }[];
};
export const getSovProgress = (pid: string) =>
  api<{ progress: SovProgress | null }>(`/projects/${pid}/billing-plan/progress`);

export const listContracts = (pid: string) =>
  api<ContractSummary[]>(`${base(pid)}/contracts`);

export const getContract = (pid: string, cid: string) =>
  api<ContractDetail>(`${base(pid)}/contracts/${cid}`);

export const listNarratives = (pid: string) =>
  api<NarrativeSummary[]>(`${base(pid)}/narratives`);

export const getNarrative = (pid: string, nid: string) =>
  api<NarrativeDetail>(`${base(pid)}/narratives/${nid}`);

export const listAuditLogs = (pid: string, limit = 100) =>
  api<AuditLogRow[]>(`${base(pid)}/audit-logs?limit=${limit}`);

// Re-exported from `./fmt` so existing imports from "@/lib/workbench"
// keep working. Client-side code should import from "@/lib/fmt" directly
// to avoid dragging in api.ts (server-only) via this module.
export { fmtMoney, fmtDate } from "./fmt";


/**
 * Scopes, fetched SERVER-side.
 *
 * `@/lib/scopes` is the client-safe wrapper and fetches a relative URL, which
 * throws in Node. A server component needs this one, which goes through `api`
 * and so builds an absolute URL and forwards the session cookie.
 */
export type ScopeSummary = {
  id: string;
  version: number;
  created_at: string | null;
};

export function listScopes(projectId: string): Promise<ScopeSummary[]> {
  return api<ScopeSummary[]>(`/scopes?project_id=${encodeURIComponent(projectId)}`);
}
