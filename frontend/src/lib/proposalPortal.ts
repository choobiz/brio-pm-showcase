/** Client › Proposal (office side) — backend services/proposal_portal. */
import { workspaceGet, workspacePatch, workspacePost } from "@/lib/workspaceApi";

export interface ProposalStatus {
  id: string;
  version_number: number;
  label: string | null;
  total: number | null;
  sent_at: string | null;
  withdrawn_at: string | null;
  in_portal: boolean;
  valid_until: string | null;
  expired: boolean;
  accepted: { typed_name: string; accepted_at: string; total: number | null; ip: string | null;
              options: { name: string; amount: number }[] } | null;
  opened_at: string | null;
  open_count: number;
  change_from_previous: number | null;
  headline: string | null;
  overview: string | null;
  closingNote: string | null;
  stale: { estimate_version: number | null; estimate_total: number | null } | null;
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/proposals`;
export const proposalStatus = (pid: string) => workspaceGet<ProposalStatus[]>(base(pid));
export const sendProposal = (pid: string, prid: string) => workspacePost<ProposalStatus[]>(`${base(pid)}/${prid}/send`);
export const withdrawProposal = (pid: string, prid: string) => workspacePost<ProposalStatus[]>(`${base(pid)}/${prid}/withdraw`);
export const editProposalText = (pid: string, prid: string, body: { headline?: string; overview?: string; closingNote?: string }) =>
  workspacePatch<ProposalStatus[]>(`${base(pid)}/${prid}/text`, body);
