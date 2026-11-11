"use client";

import { useEffect, useState } from "react";

import { getJobCost, type AIJobKind } from "./aiJobs";

/** The up-to cost of an AI job of `kind` on this project, or null while it
 *  loads or if it could not be read. A button that needs it stays disabled
 *  until it arrives: nothing runs on a cost nobody saw. */
export function useJobCost(projectId: string, kind: AIJobKind): number | null {
  const [usd, setUsd] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    getJobCost(projectId, kind)
      .then((c) => live && setUsd(c.usd))
      .catch(() => live && setUsd(null));
    return () => {
      live = false;
    };
  }, [projectId, kind]);
  return usd;
}
