// New-project setup guide — next step + type-tailored document checklist.

const BASE = "/api/backend";

export interface GsDoc {
  label: string;
  category: string;
  why: string;
  required: boolean;
}

export interface GettingStarted {
  project_type: string | null;
  bucket: "new_construction" | "renovation" | "general";
  state: {
    has_documents: boolean;
    document_count: number;
    has_scope: boolean;
    has_estimate: boolean;
  };
  next_step: "add_documents" | "generate_scope" | "generate_estimate" | "done";
  steps: { key: string; label: string; done: boolean }[];
  documents: GsDoc[];
}

export async function getGettingStarted(projectId: string): Promise<GettingStarted> {
  const r = await fetch(`${BASE}/projects/${projectId}/getting-started`, { cache: "no-store" });
  if (!r.ok) throw new Error(`getting-started ${r.status}`);
  return r.json();
}
