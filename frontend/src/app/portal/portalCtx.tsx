"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { listMyProjects, type PortalProject } from "@/lib/portal";

type Ctx = {
  project: PortalProject | null;
  projects: PortalProject[];
  setProject: (p: PortalProject) => void;
  loading: boolean;
  denied: boolean;
};

const PortalContext = createContext<Ctx>({
  project: null, projects: [], setProject: () => {}, loading: true, denied: false,
});

export const usePortal = () => useContext(PortalContext);

export function PortalProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<PortalProject[]>([]);
  const [project, setProject] = useState<PortalProject | null>(null);
  const [loading, setLoading] = useState(true);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    listMyProjects()
      .then((ps) => {
        setProjects(ps);
        setProject(ps[0] ?? null);
      })
      .catch((e: Error & { status?: number }) => {
        if (e.status === 403) setDenied(true);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <PortalContext.Provider value={{ project, projects, setProject, loading, denied }}>
      {children}
    </PortalContext.Provider>
  );
}
