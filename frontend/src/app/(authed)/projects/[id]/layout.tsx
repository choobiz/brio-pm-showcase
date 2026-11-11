import { ProjectGate } from "./ProjectGate";
import { ProjectTabs } from "./ProjectTabs";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <ProjectGate id={id}>
      <div className="min-w-0">
        <ProjectTabs id={id} />
        {children}
      </div>
    </ProjectGate>
  );
}
