/** Contract › checks, time extension, signed copy, amend (R2Contract). */
import { workspaceGet, workspacePost, workspacePut } from "@/lib/workspaceApi";

export interface ContractChecks {
  contract: { id: string; status: string; reference: string; signed_copy_document_id: string | null } | null;
  checks: { kind: "stages" | "gst" | "signed_copy"; text: string }[];
  late: { contract_date: string; expected: string; days: number; passed: boolean; extension_co: number | null } | null;
}
const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/contract`;
export const getContractChecks = (pid: string) => workspaceGet<ContractChecks>(`${base(pid)}/checks`);
export const draftTimeExtension = (pid: string) =>
  workspacePost<{ id: string; number: number; schedule_impact_days: number }>(`${base(pid)}/time-extension`);
export const setSignedCopy = (pid: string, documentId: string) =>
  workspacePut<ContractChecks>(`${base(pid)}/signed-copy`, { document_id: documentId });
export const amendContract = (pid: string) =>
  workspacePost<{ id: string; version_number: number; reference: string }>(`${base(pid)}/amend`);
