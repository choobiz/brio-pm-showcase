// A message drafted elsewhere (Schedule › Record what happened) and waiting,
// unsent, in the project's Client › Conversation reply box. Browser storage: the draft
// belongs to the person who wrote it, on this browser, until they send or
// discard it. Nothing here posts to the thread.

const key = (pid: string) => `brio:message-draft:${pid}`;

export function putMessageDraft(pid: string, body: string): void {
  try { localStorage.setItem(key(pid), body); } catch { /* storage blocked: the page shows the text to copy */ }
}

export function takeMessageDraft(pid: string): string | null {
  try { return localStorage.getItem(key(pid)); } catch { return null; }
}

export function clearMessageDraft(pid: string): void {
  try { localStorage.removeItem(key(pid)); } catch { /* nothing to clear */ }
}
