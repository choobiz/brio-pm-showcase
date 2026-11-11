/** Prepared documents (Ask → Prepare): takeoffs, orders, rental requests and
 *  field sheets drafted from a project's documents, checked, then filed as
 *  BRIO PDFs. Drafting runs on Amir's Mac when it is on, else on the server
 *  under the monthly budget. */
import { workspaceGet, workspacePatch, workspacePost, workspacePut } from "@/lib/workspaceApi";

export type PrepStatus = "queued" | "running" | "waiting_user" | "finalized" | "failed" | "cancelled";

export interface PrepRow {
  item?: string;
  spec?: string;
  qty?: string | number | null;
  unit?: string;
  basis?: string;
  source?: string;
  label?: string;
  value?: string;
}

export interface PrepSection {
  heading: string;
  kind: "table" | "pairs" | "text";
  rows: PrepRow[];
  text?: string;
}

export interface PrepPayload {
  supplier?: string | null;
  header: { label: string; value: string }[];
  sections: PrepSection[];
  notes?: string;
}

export interface PreparedDoc {
  id: string;
  kind: string;
  title: string;
  payload: PrepPayload;
  version: number;
  status: "draft" | "final";
  document_id: string | null;
}

export interface PrepQuestion {
  id: string;
  question: string;
  why?: string;
  suggested?: string;
}

export interface PrepRequest {
  id: string;
  project_id: string;
  request: string;
  document_ids: string[];
  status: PrepStatus;
  stage: "plan" | "draft" | "revise";
  engine: "mac" | "api" | null;
  plan: {
    summary?: string;
    documents?: { kind: string; title: string; supplier?: string | null }[];
    sources?: { document_id?: string; label?: string; why?: string }[];
    questions?: PrepQuestion[];
    conflicts?: string[];
    assumptions?: string[];
    geometry_to_confirm?: boolean;
  } | null;
  answers: Record<string, string> | null;
  progress: { at: string; msg: string }[];
  error: string | null;
  cost_usd: number;
  approved_usd: number;
  created_at: string | null;
  mac_available: boolean;
  documents: PreparedDoc[];
  geometry: GeometryState | null;
}

export interface GeometryFace {
  name: string;
  length_ft: number;
}

export interface Geometry {
  faces: GeometryFace[];
  outside_corners: number;
  inside_corners: number;
  wall_thickness_in: number;
  footing_width_in: number;
  footing_depth_in: number;
  wall_height_ft: number;
  interior_walls: { name: string; length_ft: number; footing: boolean }[];
  t_junctions: number;
  footing_bars: number;
  bar_size: string;
  lap_in: number;
  bar_stock_ft: number;
  notes: string;
}

export interface CalcRow extends PrepRow {
  calc: string;
}

export interface GeometryState {
  version: number;
  status: "proposed" | "confirmed";
  payload: Partial<Geometry>;
  confirmed_version: number | null;
  confirmed_at: string | null;
  calculated: {
    perimeter_ft: number;
    form_line_lf: number;
    lumber: CalcRow[];
    wall_forms: CalcRow[];
    concrete: CalcRow[];
  } | null;
}

export interface CostEstimate {
  stage: string;
  server_usd: number;
  mac_available: boolean;
  lines: { label: string; usd: number; how: string }[];
}

export interface PrepListItem {
  id: string;
  request: string;
  status: PrepStatus;
  stage: string;
  engine: string | null;
  cost_usd: number;
  created_at: string | null;
}

export interface PrepBudget {
  month_start: string;
  cap_usd: number;
  prep_usd: number;
  ask_usd: number;
  remaining_usd: number;
  can_change_cap: boolean;
  mac_available: boolean;
  mac_last_seen: string | null;
}

export interface Supplier {
  id: string;
  name: string;
  trade: string | null;
  contact_name: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  notes: string | null;
  active: boolean;
}

export interface Practice {
  id: string;
  topic: string;
  rule: string;
  source: string | null;
  active: boolean;
  created_at: string | null;
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/prep`;

export const listPrep = (pid: string) => workspaceGet<PrepListItem[]>(base(pid));
export const createPrep = (pid: string, request: string, documentIds: string[], approveUsd: number) =>
  workspacePost<PrepRequest>(base(pid), { request, document_ids: documentIds, approve_usd: approveUsd });
export const estimateNew = (pid: string, documentIds: string[]) =>
  workspaceGet<CostEstimate>(`${base(pid)}/estimate?document_ids=${documentIds.join(",")}`);
export const estimateStep = (pid: string, rid: string, stage: "plan" | "draft" | "revise") =>
  workspaceGet<CostEstimate>(`${base(pid)}/${rid}/estimate?stage=${stage}`);
export const getGeometry = (pid: string) =>
  workspaceGet<GeometryState | null>(`/projects/${encodeURIComponent(pid)}/geometry`);
export const confirmGeometry = (pid: string, payload: Partial<Geometry>) =>
  workspacePut<GeometryState>(`/projects/${encodeURIComponent(pid)}/geometry`, { payload });
export const getFormingParams = () => workspaceGet<Record<string, unknown>>("/prep/forming-params");
export const setFormingParams = (p: Record<string, unknown>) =>
  workspacePut<Record<string, unknown>>("/prep/forming-params", p);
export const getPrep = (pid: string, rid: string) => workspaceGet<PrepRequest>(`${base(pid)}/${rid}`);
export const answerPrep = (pid: string, rid: string, answers: Record<string, string>, approveUsd: number) =>
  workspacePost<PrepRequest>(`${base(pid)}/${rid}/answers`, { answers, approve_usd: approveUsd });
export const revisePrep = (pid: string, rid: string, correction: string, remember: boolean, approveUsd: number) =>
  workspacePost<PrepRequest>(`${base(pid)}/${rid}/revise`, { correction, remember, approve_usd: approveUsd });
export const editPrepDoc = (pid: string, rid: string, did: string, body: Partial<PrepPayload> & { title?: string }) =>
  workspacePatch<PrepRequest>(`${base(pid)}/${rid}/documents/${did}`, body);
export const finalizePrep = (pid: string, rid: string) => workspacePost<PrepRequest>(`${base(pid)}/${rid}/finalize`);
export const retryPrep = (pid: string, rid: string, approveUsd: number) =>
  workspacePost<PrepRequest>(`${base(pid)}/${rid}/retry`, { approve_usd: approveUsd });
export const cancelPrep = (pid: string, rid: string) => workspacePost<PrepRequest>(`${base(pid)}/${rid}/cancel`);
export const previewUrl = (pid: string, rid: string, did: string) =>
  `/api/backend${base(pid)}/${rid}/documents/${did}/preview`;

export const getBudget = () => workspaceGet<PrepBudget>("/prep/status");
export const setCap = (usd: number) => workspacePut<PrepBudget>("/prep/cap", { usd });

export const listSuppliers = () => workspaceGet<Supplier[]>("/suppliers");
export const saveSupplier = (s: Omit<Supplier, "id"> & { id?: string }) =>
  s.id ? workspacePut<Supplier>(`/suppliers/${s.id}`, s) : workspacePost<Supplier>("/suppliers", s);

export const listPractices = () => workspaceGet<Practice[]>("/practices");
export const savePractice = (p: { id?: string; topic: string; rule: string; active: boolean }) =>
  p.id ? workspacePut<Practice>(`/practices/${p.id}`, p) : workspacePost<Practice>("/practices", p);

export const STATUS_LABEL: Record<PrepStatus, string> = {
  queued: "Waiting to start",
  running: "Working",
  waiting_user: "Needs you",
  finalized: "Finalised",
  failed: "Failed",
  cancelled: "Cancelled",
};
