/** Ask — the assistant panel (UI round 1, phase 9). Read-only answers. */
import { workspaceGet, workspacePost } from "@/lib/workspaceApi";

export interface AskSource {
  label: string;
  href: string;
  updated_at: string | null;
}

export interface AskAnswer {
  answer: string;
  sources: AskSource[];
  tools: string[];
}

export interface AskTurn {
  role: "user" | "assistant";
  content: string;
}

/** The most one question should cost; asking approves it (Amir 2026-10-13). */
export const getAskEstimate = () => workspaceGet<{ usd: number; how: string }>("/assistant/estimate");

export function askAssistant(
  question: string,
  projectId: string | null,
  history: AskTurn[],
  approveUsd: number,
): Promise<AskAnswer> {
  return workspacePost<AskAnswer>("/assistant/ask", {
    question,
    project_id: projectId,
    history: history.slice(-6),
    approve_usd: approveUsd,
  });
}
