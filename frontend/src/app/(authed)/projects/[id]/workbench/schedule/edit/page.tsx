import { listSchedules, getSchedule } from "@/lib/workbench";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";
import { ScheduleEditor } from "./ScheduleEditor";
import type { CpmActivity } from "@/lib/cpm";

export default async function EditSchedulePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const res = await loadOr("schedules", listSchedules(id));
  if (res.failed) return <LoadFailed what="the schedule" />;
  const list = res.data ?? [];
  const latest = list[list.length - 1];
  if (!latest) {
    return (
      <div className="text-sm text-slate-500 italic">
        No schedule to edit yet for this project.
      </div>
    );
  }
  const sched = await getSchedule(id, latest.id);

  return (
    <ScheduleEditor
      projectId={id}
      scheduleId={sched.id}
      initialActivities={sched.activities as unknown as CpmActivity[]}
      initialInspections={(sched.inspections as unknown as {
        activity_code?: string;
        authority?: string;
        inspection_type?: string;
        lead_time_days?: number;
        gates?: string;
      }[]) ?? []}
      projectStart={sched.project_start}
      workingHours={sched.working_hours}
      holidays={(sched.holidays as unknown as string[]) ?? []}
    />
  );
}
