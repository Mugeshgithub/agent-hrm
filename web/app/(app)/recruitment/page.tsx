"use client";
// Recruitment: the jobs HR and the hiring managers hire for. HR sees every job, a hiring manager only their own. The app informs and a person
// decides: nobody is ranked or scored here, and only a person moves a candidate (see the job's page).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Plus } from "lucide-react";
import { fetcher } from "@/lib/api";
import { Job } from "@/lib/types";
import { Counts, JobStatus } from "@/components/JobParts";
import { JobFormDialog } from "@/components/JobForm";
import { RetentionCard } from "@/components/RetentionCard";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

export default function Recruitment() {
  const me = useMe();
  const allowed = me.role === "hr" || me.role === "manager";
  const { data, error } = useSWR<Job[]>(allowed ? "/recruitment/jobs" : null, fetcher);
  const [creating, setCreating] = useState(false);
  const router = useRouter();
  if (!allowed) return <Empty title="Recruitment is for HR and hiring managers">Jobs and candidates are handled by HR and the hiring manager.</Empty>;

  const isHR = me.role === "hr";
  return (
    <>
      <PageHeader title={isHR ? "Recruitment" : "Hiring"}
        description={isHR ? "Every job, and the people who applied. A person moves each candidate from one stage to the next; the app never ranks, scores or rejects anyone."
          : "The jobs you hire for, and the people who applied. You move each candidate from one stage to the next; the app never ranks, scores or rejects anyone."}
        actions={<>
          <a className="btn-quiet" href="/careers" target="_blank" rel="noreferrer">Careers page</a>
          <button className="btn-primary" onClick={() => setCreating(true)}><Plus size={16} aria-hidden /> New job</button>
        </>} />
      {error && <ErrorNote message={error.message} />}
      {data && data.length === 0 && <div className="panel"><Empty title="No jobs yet">Create a job to start. It begins as a draft{isHR ? "" : ", and HR opens it when it is ready"}.</Empty></div>}
      {!!data?.length && (
        <ul className="panel divide-y divide-rule">
          {data.map((j) => (
            <li key={j.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <Link href={`/recruitment/${j.id}`} className="font-medium hover:text-dossier hover:underline">{j.title}</Link>
                  <JobStatus value={j.status} />
                  {j.new > 0 && <span className="rounded-full bg-dossier-tint px-2 text-sm font-medium text-dossier">{j.new} new</span>}
                </p>
                <p className="text-sm text-ink-soft">{j.team}, {j.location}, {j.contract_type}{isHR && j.hiring_manager_name ? `. Hiring manager: ${j.hiring_manager_name}` : ""}</p>
              </div>
              {j.status !== "draft" && <Counts counts={j.counts} />}
            </li>
          ))}
        </ul>
      )}
      {isHR && <RetentionCard />}
      {creating && <JobFormDialog job={null} onClose={() => setCreating(false)} onSaved={(j) => router.push(`/recruitment/${j.id}`)} />}
    </>
  );
}
