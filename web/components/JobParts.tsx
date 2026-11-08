"use client";
// Small pieces shared by the recruitment pages: the stage names, the job's status, its counts and its salary range.
import { Job, JobStage } from "@/lib/types";

export const STAGE: Record<JobStage, string> = {
  applied: "Applied", screening: "Screening", interview: "Interview", offer: "Offer", hired: "Hired", not_selected: "Not selected",
};
export const ACTIVE_STAGES: JobStage[] = ["applied", "screening", "interview", "offer"];
export const ALL_STAGES: JobStage[] = [...ACTIVE_STAGES, "hired", "not_selected"];

const STATUS = {
  draft: { label: "Draft", color: "bg-off" },
  open: { label: "Open", color: "bg-ok" },
  closed: { label: "Closed", color: "bg-off" },
};

export function JobStatus({ value }: { value: Job["status"] }) {
  const s = STATUS[value];
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-ink whitespace-nowrap">
      <span className={`h-2 w-2 rounded-full ${s.color}`} aria-hidden />
      {s.label}
    </span>
  );
}

export function salaryText(job: Pick<Job, "salary_min" | "salary_max">) {
  if (job.salary_min == null || job.salary_max == null) return null;
  const k = (n: number) => `€${n.toLocaleString("en-GB")}`;
  return `${k(job.salary_min)} to ${k(job.salary_max)} gross a year`;
}

// how many people are at each stage, in the order they happen. Counts only: no one is ranked or scored.
export function Counts({ counts, only = ACTIVE_STAGES }: { counts: Job["counts"]; only?: JobStage[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-soft">
      {only.map((s) => <li key={s}><span className="font-medium text-ink">{counts[s]}</span> {STAGE[s]}</li>)}
    </ul>
  );
}
