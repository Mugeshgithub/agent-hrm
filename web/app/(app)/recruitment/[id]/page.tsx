"use client";
// One job and the people who applied to it. A person moves each candidate from one stage to the next; "Not selected" always needs an internal
// reason (the candidate never sees it); every move is logged. Candidates are listed in the order they applied: the app has no score, rank or
// recommendation, so there is nothing to sort by. Closing a job rejects nobody.
import Link from "next/link";
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { ArrowLeft, Pencil, Plus } from "lucide-react";
import { api, ApiError, fetcher } from "@/lib/api";
import { day, timestamp } from "@/lib/format";
import { Candidate, CandidateEvent, JobStage, Pipeline } from "@/lib/types";
import { ACTIVE_STAGES, ALL_STAGES, Counts, JobStatus, salaryText, STAGE } from "@/components/JobParts";
import { JobFormDialog } from "@/components/JobForm";
import { Dialog } from "@/components/Dialog";
import { Avatar } from "@/components/Shell";
import { Empty, ErrorNote } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

export default function JobPage({ params }: { params: { id: string } }) {
  const me = useMe();
  const allowed = me.role === "hr" || me.role === "manager";
  const { data, error } = useSWR<Pipeline>(allowed ? `/recruitment/jobs/${params.id}` : null, fetcher);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [moving, setMoving] = useState<Candidate | null>(null);
  const [viewing, setViewing] = useState<Candidate | null>(null);
  const [linking, setLinking] = useState<Candidate | null>(null);
  const [problem, setProblem] = useState("");

  if (!allowed || (error instanceof ApiError && (error.status === 404 || error.status === 403))) {
    return <Empty title="No such job">It doesn't exist, or it isn't one you hire for.</Empty>;
  }
  if (error) return <ErrorNote message={error.message} />;
  if (!data) return <p className="text-ink-soft">Loading…</p>;

  const { job, candidates, events } = data;
  const by = (stage: JobStage) => candidates.filter((c) => c.stage === stage);       // the order they applied
  const salary = salaryText(job);

  async function setStatus(status: "open" | "closed") {
    setProblem("");
    try {
      await api(`/recruitment/jobs/${job.id}/status`, { body: { status } });
      mutate(() => true);
    } catch (e) { setProblem((e as Error).message); }
  }

  return (
    <>
      <Link href="/recruitment" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-soft hover:text-ink"><ArrowLeft size={14} aria-hidden /> All jobs</Link>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xl font-semibold tracking-[-0.01em]">{job.title} <JobStatus value={job.status} /></h1>
          <p className="mt-1 text-ink-soft">{job.team}, {job.location}, {job.contract_type}. Hiring manager: {job.hiring_manager_name}.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {job.can_edit && <button className="btn-quiet" onClick={() => setEditing(true)}><Pencil size={15} aria-hidden /> Edit</button>}
          {job.status === "open" && <button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} aria-hidden /> Add candidate</button>}
          {job.status !== "open" && job.can_open && <button className="btn-primary" onClick={() => setStatus("open")}>{job.status === "draft" ? "Open the job" : "Reopen the job"}</button>}
          {job.status === "open" && <button className="btn-quiet" onClick={() => setStatus("closed")}>Close the job</button>}
        </div>
      </div>
      {problem && <div className="mb-4"><ErrorNote message={problem} /></div>}
      {job.status === "draft" && !job.can_open && <p className="mb-4 panel px-5 py-3 text-sm text-ink-soft">This job is a draft. HR opens it when it is ready: opening makes it public.</p>}
      {job.warnings.length > 0 && job.status !== "open" && (
        <div className="mb-4"><ErrorNote message={`The text refers to ${job.warnings.map((w) => `"${w.words}" (${w.about})`).join("; ")}. No job offer may refer to a characteristic listed in the Code du travail L1132-1 (L5321-2), so it can't be opened until this is changed.`} /></div>
      )}

      <section aria-labelledby="about" className="panel mb-6 px-5 py-4">
        <h2 id="about" className="sr-only">About the job</h2>
        <p className="whitespace-pre-line">{job.description}</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <h3 className="text-sm font-medium text-ink-soft">Must have</h3>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">{job.must_have.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
          {job.nice_to_have.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-ink-soft">Nice to have</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">{job.nice_to_have.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
        </div>
        {salary && <p className="mt-4 text-sm text-ink-soft">Salary: {salary}.</p>}
      </section>

      {job.status !== "draft" && (
        <>
          <div className="mb-3"><Counts counts={job.counts} only={ALL_STAGES} /></div>
          {job.status === "closed" && ACTIVE_STAGES.some((s) => job.counts[s] > 0) && (
            <p className="mb-3 panel px-5 py-3 text-sm text-ink-soft">This job is closed, but some people are still waiting for a decision. Closing a job rejects nobody: move each candidate yourself.</p>
          )}
          {ALL_STAGES.map((stage) => <StageSection key={stage} stage={stage} candidates={by(stage)} isHR={me.role === "hr"} onMove={setMoving} onHistory={setViewing} onLink={setLinking} />)}
        </>
      )}

      {events.length > 0 && (
        <section aria-labelledby="log" className="mt-8">
          <details>
            <summary id="log" className="cursor-pointer font-medium">Job history</summary>
            <ul className="mt-2 space-y-1 text-sm text-ink-soft">
              {events.map((e, i) => <li key={i}>{timestamp(e.created_at)}: {e.actor_name ?? "Someone"} {eventText(e.event, e.detail)}</li>)}
            </ul>
          </details>
        </section>
      )}

      {editing && <JobFormDialog job={job} onClose={() => setEditing(false)} />}
      {adding && <AddCandidateDialog jobId={job.id} onClose={() => setAdding(false)} />}
      {moving && <MoveDialog c={moving} onClose={() => setMoving(null)} />}
      {viewing && <HistoryDialog c={viewing} onClose={() => setViewing(null)} />}
      {linking && <NewLinkDialog c={linking} onClose={() => setLinking(null)} />}
    </>
  );
}

const EVENT_LABEL: Record<string, string> = {
  added: "Added", "cv opened": "CV opened", "new link": "New tracking link", "talent pool left": "Left the talent pool", erased: "Personal data erased",
};

function eventText(event: string, detail: string) {
  if (event === "created") return `created the job (${detail})`;
  if (event === "edited") return `changed ${detail}`;
  if (event === "open") return "opened the job";
  if (event === "closed") return "closed the job";
  return event;
}

function StageSection({ stage, candidates, isHR, onMove, onHistory, onLink }:
  { stage: JobStage; candidates: Candidate[]; isHR: boolean; onMove: (c: Candidate) => void; onHistory: (c: Candidate) => void; onLink: (c: Candidate) => void }) {
  const folded = stage === "not_selected" || stage === "hired";
  const list = (
    <ul className="panel divide-y divide-rule">
      {candidates.map((c) => <CandidateRow key={c.id} c={c} isHR={isHR} onMove={onMove} onHistory={onHistory} onLink={onLink} />)}
    </ul>
  );
  if (!candidates.length) return null;
  return (
    <section aria-labelledby={`stage-${stage}`} className="mb-6">
      {folded ? (
        <details>
          <summary id={`stage-${stage}`} className="cursor-pointer font-medium">{STAGE[stage]} <span className="font-normal text-ink-soft">({candidates.length})</span></summary>
          <div className="mt-2">{list}</div>
        </details>
      ) : (
        <>
          <h2 id={`stage-${stage}`} className="mb-2 font-medium">{STAGE[stage]} <span className="font-normal text-ink-soft">({candidates.length})</span></h2>
          {list}
        </>
      )}
    </section>
  );
}

function CandidateRow({ c, isHR, onMove, onHistory, onLink }:
  { c: Candidate; isHR: boolean; onMove: (c: Candidate) => void; onHistory: (c: Candidate) => void; onLink: (c: Candidate) => void }) {
  const full = (iso: string) => `${day(iso)} ${iso.slice(0, 4)}`;
  return (
    <li className="px-5 py-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3 min-w-0">
          <Avatar name={c.name} size={32} />
          <div className="min-w-0">
            <p className="font-medium">{c.name}</p>
            <p className="text-sm text-ink-soft">Applied {day(c.applied_at)}, {c.days_in_stage === 0 ? "moved here today" : `${c.days_in_stage} ${c.days_in_stage === 1 ? "day" : "days"} in this stage`}</p>
            <p className="text-sm text-ink-soft break-all">{c.email}{c.phone ? `, ${c.phone}` : ""}</p>
            <p className="text-sm text-ink-soft">
              {c.source === "careers page" ? "Applied on the careers page" : "Added by hand"}
              {c.talent_pool && c.consented_on ? `, in the talent pool (agreed ${full(c.consented_on)})` : ""}.
              {c.erase_on ? ` Personal data erased on ${full(c.erase_on)}.` : ""}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {c.has_cv && <a className="btn-quiet" href={`/api/recruitment/candidates/${c.id}/cv`}>Open CV</a>}
          {isHR && c.source === "careers page" && <button className="btn-quiet" onClick={() => onLink(c)}>New link</button>}
          <button className="btn-quiet" onClick={() => onHistory(c)}>History</button>
          {c.stage !== "hired" && <button className="btn-primary" onClick={() => onMove(c)}>Move</button>}
        </div>
      </div>
      {c.cover_letter && (
        <details className="mt-3 pl-11 text-sm">
          <summary className="cursor-pointer text-ink-soft">Cover letter</summary>
          <p className="mt-1 whitespace-pre-line">{c.cover_letter}</p>
        </details>
      )}
    </li>
  );
}

function AddCandidateDialog({ jobId, onClose }: { jobId: number; onClose: () => void }) {
  const [f, setF] = useState({ name: "", email: "", phone: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try { await api(`/recruitment/jobs/${jobId}/candidates`, { body: f }); mutate(() => true); onClose(); }
    catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <Dialog open onClose={onClose} title="Add a candidate">
      <form onSubmit={save} className="space-y-4">
        <p className="text-ink-soft">For someone who applied another way. They start in Applied; people who apply on the careers page will arrive here by themselves.</p>
        <label className="block"><span className="label">Name</span><input className="field" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="block"><span className="label">Email</span><input className="field" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
        <label className="block"><span className="label">Phone (optional)</span><input className="field" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></label>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>Add candidate</button>
        </div>
      </form>
    </Dialog>
  );
}

function MoveDialog({ c, onClose }: { c: Candidate; onClose: () => void }) {
  const choices = ALL_STAGES.filter((s) => s !== c.stage && (s !== "hired" || c.stage === "offer"));
  const [stage, setStage] = useState<JobStage>(choices.find((s) => s !== "not_selected") ?? choices[0]);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const needsReason = stage === "not_selected" || c.stage === "not_selected";
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try { await api(`/recruitment/candidates/${c.id}/move`, { body: { stage, reason } }); mutate(() => true); onClose(); }
    catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <Dialog open onClose={onClose} title={`Move ${c.name}`}>
      <form onSubmit={save} className="space-y-4">
        <p className="text-ink-soft">Now in {STAGE[c.stage]}. Moving a candidate is your decision: it is saved with your name and the time.</p>
        <label className="block"><span className="label">Move to</span>
          <select className="field" value={stage} onChange={(e) => setStage(e.target.value as JobStage)}>
            {choices.map((s) => <option key={s} value={s}>{STAGE[s]}</option>)}
          </select>
        </label>
        {needsReason && (
          <label className="block"><span className="label">{c.stage === "not_selected" ? "Why bring this person back?" : "Why is this person not selected?"}</span>
            <textarea className="field h-auto py-2 leading-relaxed" required rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="For example: does not have the SQL experience the job needs" />
            <span className="mt-1 block text-sm text-ink-soft">{c.stage === "not_selected" ? "Saved in the history." : "Internal: the candidate does not see it. Say what the job needs and what the application does not show, not who the person is."}</span>
          </label>
        )}
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || (needsReason && !reason.trim())}>Move to {STAGE[stage]}</button>
        </div>
      </form>
    </Dialog>
  );
}

// HR gives a candidate a new tracking link (the old one stops working). Only a hash is kept, so the link is shown here once, to copy into an email.
function NewLinkDialog({ c, onClose }: { c: Candidate; onClose: () => void }) {
  const [link, setLink] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  async function issue() {
    setBusy(true);
    setError("");
    try { setLink((await api<{ link: string }>(`/recruitment/candidates/${c.id}/tracking-link`, { body: {} })).link); mutate(() => true); }
    catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <Dialog open onClose={onClose} title={`New link for ${c.name}`}>
      <div className="space-y-4">
        {!link ? (
          <>
            <p className="text-ink-soft">This makes a new private link to the candidate's page and stops the old one working. The app sends no emails: you copy the link and send it yourself. It is shown once.</p>
            {error && <ErrorNote message={error} />}
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={onClose}>Cancel</button>
              <button className="btn-primary" disabled={busy} onClick={issue}>Make a new link</button>
            </div>
          </>
        ) : (
          <>
            <p className="break-all rounded border border-rule bg-paper px-3 py-2 text-sm">{link}</p>
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={async () => { try { await navigator.clipboard.writeText(link); setCopied(true); } catch {} }}>{copied ? "Copied" : "Copy the link"}</button>
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}

function HistoryDialog({ c, onClose }: { c: Candidate; onClose: () => void }) {
  const { data, error } = useSWR<CandidateEvent[]>(`/recruitment/candidates/${c.id}/history`, fetcher);
  return (
    <Dialog open onClose={onClose} title={`${c.name}: history`}>
      {error && <ErrorNote message={error.message} />}
      {!data && !error && <p className="text-ink-soft">Loading…</p>}
      {data && (
        <ol className="space-y-3">
          {data.map((e, i) => (
            <li key={i} className="text-sm">
              <p><span className="font-medium">{EVENT_LABEL[e.event] ?? `${STAGE[e.from_stage as JobStage]} to ${STAGE[e.to_stage as JobStage]}`}</span>
                <span className="text-ink-soft"> by {e.actor_name ?? "someone"}, {timestamp(e.created_at)}</span></p>
              {e.reason && <p className="text-ink-soft">{e.reason}</p>}
            </li>
          ))}
        </ol>
      )}
      <div className="mt-5 flex justify-end"><button className="btn-quiet" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}
