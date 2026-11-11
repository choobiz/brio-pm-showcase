import { redirect } from "next/navigation";

/** Round 2 (2026-11-02): Messages and Conversations are one page, Client › Conversation. */
export default async function Moved({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/projects/${id}/conversation`);
}
