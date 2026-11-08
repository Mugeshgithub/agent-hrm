"use client";
// Create or edit a job. The rules are in jobs.py: this form only collects the answers and shows the server's reason when something is refused.
// A job starts as a draft. When the text refers to a characteristic the law forbids in a job offer (L1132-1, L5321-2), the form says which
// words after saving, and HR cannot open the job until they are gone.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { Job } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

const CONTRACTS: [string, string][] = [["CDI", "Permanent (CDI)"], ["CDD", "Fixed-term (CDD)"], ["Apprenticeship", "Apprenticeship"], ["Internship", "Internship"]];

type Fields = {
  title: string; team: string; location: string; contract_type: string; description: string; must_have: string; nice_to_have: string;
  salary_min: string; salary_max: string; hiring_manager_id: string;
};

function fieldsOf(job: Job | null, department: string): Fields {
  return {
    title: job?.title ?? "", team: job?.team ?? department, location: job?.location ?? "Paris", contract_type: job?.contract_type ?? "CDI",
    description: job?.description ?? "", must_have: (job?.must_have ?? []).join("\n"), nice_to_have: (job?.nice_to_have ?? []).join("\n"),
    salary_min: job?.salary_min?.toString() ?? "", salary_max: job?.salary_max?.toString() ?? "", hiring_manager_id: job?.hiring_manager_id ?? "",
  };
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-sm text-ink-soft">{hint}</span>}
    </label>
  );
}

export function JobFormDialog({ job, onClose, onSaved }: { job: Job | null; onClose: () => void; onSaved?: (job: Job) => void }) {
  const me = useMe();
  const isHR = me.role === "hr";
  const { data: managers } = useSWR<{ id: string; name: string; role: string }[]>(isHR ? "/recruitment/hiring-managers" : null, fetcher);
  const [f, setF] = useState<Fields>(() => fieldsOf(job, me.department));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof Fields, value: string) => setF((x) => ({ ...x, [key]: value }));

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = {
        ...f, must_have: f.must_have, nice_to_have: f.nice_to_have,
        salary_min: f.salary_min === "" ? null : Number(f.salary_min), salary_max: f.salary_max === "" ? null : Number(f.salary_max),
        hiring_manager_id: isHR ? f.hiring_manager_id : undefined,
      };
      const result = job ? await api<Job>(`/recruitment/jobs/${job.id}`, { method: "PATCH", body }) : await api<Job>("/recruitment/jobs", { body });
      mutate(() => true);
      onSaved?.(result);
      if (result.warnings.length) setSaved(result);    // stay open: say which words to change
      else onClose();
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  if (saved) {
    return (
      <Dialog open onClose={onClose} title="Saved, with a note" width="max-w-xl">
        <div className="space-y-4">
          <p>The job is saved as it is, but this text refers to something a job offer may not mention (Code du travail, L5321-2 and L1132-1). HR can't open the job until it is changed:</p>
          <ul className="list-disc space-y-1 pl-5">
            {saved.warnings.map((w, i) => <li key={i}>"{w.words}": {w.about}</li>)}
          </ul>
          <div className="flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog open onClose={onClose} title={job ? "Edit job" : "New job"} width="max-w-2xl">
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Title"><input className="field" required maxLength={120} value={f.title} onChange={(e) => set("title", e.target.value)} placeholder="Data Analyst" /></Field>
          <Field label="Contract">
            <select className="field" value={f.contract_type} onChange={(e) => set("contract_type", e.target.value)}>
              {CONTRACTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Team"><input className="field" required maxLength={80} value={f.team} onChange={(e) => set("team", e.target.value)} /></Field>
          <Field label="Location"><input className="field" required maxLength={80} value={f.location} onChange={(e) => set("location", e.target.value)} /></Field>
          {isHR && (
            <Field label="Hiring manager" hint="They see this job's candidates and can move them.">
              <select className="field" required value={f.hiring_manager_id} onChange={(e) => set("hiring_manager_id", e.target.value)}>
                <option value="">Choose…</option>
                {(managers ?? []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </Field>
          )}
          <div className="grid grid-cols-2 gap-4">
            <Field label="Salary from (optional)"><input className="field" inputMode="numeric" value={f.salary_min} onChange={(e) => set("salary_min", e.target.value)} placeholder="42000" /></Field>
            <Field label="to"><input className="field" inputMode="numeric" value={f.salary_max} onChange={(e) => set("salary_max", e.target.value)} placeholder="52000" /></Field>
          </div>
        </div>
        <p className="-mt-2 text-sm text-ink-soft">Salary is gross euros a year. Give both ends, or leave both empty.</p>
        <Field label="Description"><textarea className="field h-auto py-2 leading-relaxed" required rows={5} maxLength={4000} value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Must-have requirements" hint="One per line. Each application is read against these, one by one.">
            <textarea className="field h-auto py-2 leading-relaxed" required rows={5} value={f.must_have} onChange={(e) => set("must_have", e.target.value)} placeholder={"2 years of data analysis\nSQL"} />
          </Field>
          <Field label="Nice-to-have (optional)" hint="One per line.">
            <textarea className="field h-auto py-2 leading-relaxed" rows={5} value={f.nice_to_have} onChange={(e) => set("nice_to_have", e.target.value)} placeholder="dbt" />
          </Field>
        </div>
        <p className="text-sm text-ink-soft">Ask for skills and experience only. A job offer may not mention age, sex, family situation, origin, health, appearance and the like.</p>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{job ? "Save changes" : "Create draft"}</button>
        </div>
      </form>
    </Dialog>
  );
}
