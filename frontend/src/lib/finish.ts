/** Finishes asked and confirmed — backend services/finish_spec. */
import { workspaceGet, workspacePatch, workspacePost, workspacePut } from "@/lib/workspaceApi";

export type DeliveredAs = "finished" | "shell" | "part_shell";

export interface FinishQuestion {
  key: string;
  label: string;
  question: string;
  answer: string | null;
  suggested: string | null;
  why: string | null;
  confirmed: boolean;
  confirmed_at: string | null;
}

export interface FinishState {
  delivered_as: DeliveredAs | null;
  part_shell_note: string | null;
  asks_finishes: boolean;
  questions: FinishQuestion[];
  confirmed: number;
  total: number;
  shell_excluded: string[];
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/finish`;

export const getFinish = (pid: string) => workspaceGet<FinishState>(base(pid));
export const setDelivery = (pid: string, body: { delivered_as?: DeliveredAs | null; part_shell_note?: string | null }) =>
  workspacePatch<FinishState>(base(pid), body);
export const answerFinish = (pid: string, key: string, answer: string, confirmed: boolean) =>
  workspacePut<FinishState>(`${base(pid)}/${encodeURIComponent(key)}`, { answer, confirmed });
export const suggestFinishes = (pid: string) => workspacePost<FinishState>(`${base(pid)}/suggest`);
