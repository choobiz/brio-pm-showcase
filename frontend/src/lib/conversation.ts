/** Client › Conversation — backend services/conversation. The portal thread
 *  and logged emails/calls/notes as one timeline. Reading it marks the
 *  client's portal messages read. */
import { workspaceGet, workspacePatch } from "@/lib/workspaceApi";

export type ConvGroup = "client" | "engineers" | "suppliers" | "other";

export interface ConvItem {
  kind: "portal" | "comm";
  id: string;
  at: string | null;
  group: ConvGroup;
  ours: boolean;
  author: string;
  counterpart?: string | null;
  channel: string;
  source?: string;
  subject: string | null;
  body: string;
  summary: string | null;
  category: string | null;
  urgency?: string | null;
  action_items: string[];
  read: boolean;
  needs_reply: boolean;
  /** A reply filed against a booking still waiting on its party. */
  booking?: { id: string; ref: string; title: string };
}

export interface Conversation {
  client: { name: string | null; emails: string[] };
  items: ConvItem[];
  counts: Record<ConvGroup, number>;
  needs_reply: number;
  open_questions: { item: string; id: string; kind: string; at: string | null; author: string }[];
  decisions: { what: string; where: string; at: string | null }[];
}

export const getConversation = (pid: string) =>
  workspaceGet<Conversation>(`/projects/${encodeURIComponent(pid)}/conversation`);

export const getScheduleReply = (pid: string) =>
  workspaceGet<{ text: string }>(`/projects/${encodeURIComponent(pid)}/conversation/schedule-reply`);
export const markBooked = (pid: string, mid: string) =>
  workspacePatch<unknown>(`/projects/${encodeURIComponent(pid)}/milestones/${mid}`, { status: "booked" });
