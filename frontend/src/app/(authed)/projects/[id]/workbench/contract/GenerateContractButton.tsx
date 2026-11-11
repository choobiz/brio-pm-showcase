"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { generateContractAction } from "./actions";

/** Creates a contract record from the active estimate + schedule, then the
 *  status lifecycle can drive it. Shown when no contract exists yet. */
export function GenerateContractButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [err, setErr] = useState<string | null>(null);

  return (
    <div className="space-y-2">
      <Button
        onClick={() => {
          setErr(null);
          startTransition(async () => {
            try {
              await generateContractAction(projectId);
              router.refresh();
            } catch (e) {
              setErr(String(e));
            }
          });
        }}
        disabled={pending}
      >
        {pending ? "Generating…" : "Generate contract"}
      </Button>
      {err && <div className="text-xs text-red-600">{err}</div>}
    </div>
  );
}
