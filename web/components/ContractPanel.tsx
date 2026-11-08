"use client";
// HR's contract box on a new hire's onboarding card: draft it (the legal maximum for the trial period is shown while
// typing), send it to the hire's manager, then download the PDF once it is approved and mark it signed. The rules are
// in contracts.py: the app never sends a contract; the PDF only exists after the approval, and each download is logged.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { day, timestamp } from "@/lib/format";
import { Contract, ContractPreview, TrialLimit } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";
import { TextDialog } from "@/components/TextDialog";

const CATEGORY: Record<string, string> = { employee: "Ouvrier ou employé", technician: "Agent de maîtrise ou technicien", executive: "Cadre" };
const euro = (n: number) => n.toLocaleString("fr-FR", { style: "currency", currency: "EUR" });
const STATUS_TEXT: Record<string, string> = {
  draft: "Draft", pending_approval: "Waiting for approval", approved: "Approved", rejected: "Rejected, HR has to change it", signed: "Signed", cancelled: "Cancelled",
};
const DOT: Record<string, string> = { draft: "bg-off", pending_approval: "bg-wait", approved: "bg-ok", rejected: "bg-no", signed: "bg-ok", cancelled: "bg-off" };

export function ContractPanel({ empId, firstName, contractType, startDate }: { empId: string; firstName: string; contractType: string; startDate: string }) {
  const { data: c } = useSWR<Contract | null>(`/hr/people/${empId}/contract`, fetcher);
  const [form, setForm] = useState(false);
  const [asking, setAsking] = useState<"cancel" | "signed" | null>(null);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const supported = contractType === "CDI" || contractType === "CDD";
  const live = c && c.status !== "cancelled" ? c : null;

  async function submit() {
    if (!live) return;
    setBusy(true);
    setError("");
    try { await api(`/hr/contracts/${live.id}/submit`, { body: {} }); await mutate(() => true); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-5 mt-4 rounded border border-rule px-4 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-medium">Employment contract</p>
          {!supported ? <p className="text-sm text-ink-soft">A {contractType} isn't drafted here: apprenticeships and internships use other documents.</p>
            : !live ? <p className="text-sm text-ink-soft">No draft yet. The app writes a draft from {firstName}'s record, in French, and checks the trial period against the law. A manager approves it before it can leave the app.</p>
            : (
              <p className="inline-flex flex-wrap items-center gap-x-2 text-sm">
                <span className={`h-2 w-2 rounded-full ${DOT[live.status]}`} aria-hidden />
                <span>{STATUS_TEXT[live.status]}</span>
                <span className="text-ink-soft">
                  {live.status === "pending_approval" && `· waiting for ${live.approver}`}
                  {live.status === "approved" && `· approved by ${live.decided_by}`}
                  {live.status === "signed" && `· signed on ${day(live.signed_on!)}`}
                  {live.status === "draft" && `· salary ${euro(live.fields.salary_eur)}, trial period ${live.trial_text}`}
                </span>
              </p>
            )}
        </div>
        {supported && (
          <div className="flex shrink-0 flex-wrap gap-2">
            {!live && <button className="btn-quiet" onClick={() => setForm(true)}>Draft the contract</button>}
            {live && ["draft", "rejected", "pending_approval"].includes(live.status) && <button className="btn-ghost" onClick={() => setForm(true)}>{live.status === "pending_approval" ? "Pull back and edit" : "Edit"}</button>}
            {live && live.status === "draft" && <button className="btn-primary" disabled={busy || live.stale} onClick={submit}>Send to {live.approver ?? "the manager"} for approval</button>}
            {live && live.can_download && <a className="btn-quiet" href={`/api/hr/contracts/${live.id}/pdf`}>Download the PDF</a>}
            {live && live.status === "approved" && <button className="btn-primary" onClick={() => setAsking("signed")}>Mark as signed</button>}
            {live && live.status !== "signed" && <button className="btn-ghost" onClick={() => setAsking("cancel")}>Cancel</button>}
          </div>
        )}
      </div>
      {live?.stale && <div className="mt-2"><ErrorNote message="The person's details changed since this draft was written. Edit and save it again to bring it up to date." /></div>}
      {live?.status === "rejected" && <p className="mt-2 text-sm text-no">{live.decided_by} rejected it: {live.decision_note}</p>}
      {live?.status === "pending_approval" && <p className="mt-1 text-sm text-ink-soft">Nothing leaves the app until {live.approver} approves it.</p>}
      {live?.status === "approved" && <p className="mt-1 text-sm text-ink-soft">Download it, send it and have it signed outside the app, then mark it as signed here. Every download is logged{live.downloads.length ? ` (${live.downloads.length} so far, last by ${live.downloads[0].viewer_name} on ${timestamp(live.downloads[0].downloaded_at)})` : ""}.</p>}
      {error && <div className="mt-2"><ErrorNote message={error} /></div>}
      {live && (
        <p className="mt-1 text-sm"><button className="text-dossier hover:underline" onClick={() => setPreview((p) => !p)}>{preview ? "Hide the text" : "Read the text"}</button></p>
      )}
      {live && preview && <PreviewText id={live.id} />}
      {form && <ContractForm empId={empId} contractType={contractType} startDate={startDate} existing={live} onClose={() => setForm(false)} />}
      {asking === "cancel" && live && (
        <TextDialog title="Cancel this contract" danger submit="Cancel the contract" field="reason" endpoint={`/hr/contracts/${live.id}/cancel`}
                    intro="It can't be approved or downloaded any more. You can write a new one afterwards." placeholder="For example: the terms changed" onClose={() => setAsking(null)} />
      )}
      {asking === "signed" && live && <SignedDialog id={live.id} onClose={() => setAsking(null)} />}
    </div>
  );
}

export function PreviewText({ id }: { id: number }) {
  const { data, error } = useSWR<ContractPreview>(`/contracts/${id}/preview`, fetcher);
  if (error) return <ErrorNote message={error.message} />;
  if (!data) return <p className="mt-2 text-sm text-ink-soft">Loading…</p>;
  return (
    <div className="mt-2 max-h-80 overflow-y-auto rounded border border-rule bg-paper px-4 py-3 text-sm leading-relaxed">
      {data.paragraphs.map((p, i) => (
        <p key={i} className={p.style === "h1" ? "mb-2 font-semibold" : p.style === "h2" ? "mt-3 font-medium" : p.style === "b" ? "mt-2 font-medium" : p.style === "small" ? "text-xs text-ink-soft" : "mt-1"}>{p.text}</p>
      ))}
      <p className="mt-3 text-xs text-ink-soft">To complete before signing: {data.placeholders.join(", ")}.</p>
    </div>
  );
}

function ContractForm({ empId, contractType, startDate, existing, onClose }:
  { empId: string; contractType: string; startDate: string; existing: Contract | null; onClose: () => void }) {
  const f = existing?.fields;
  const cdi = contractType === "CDI";
  const [salary, setSalary] = useState(f ? String(f.salary_eur) : "");
  const [hours, setHours] = useState(String(f?.weekly_hours ?? 35));
  const [category, setCategory] = useState(f?.category ?? "");
  const [trial, setTrial] = useState(String(f?.trial ?? 0));
  const [endDate, setEndDate] = useState(f?.end_date ?? "");
  const [reason, setReason] = useState(f?.cdd_reason ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { data: limit } = useSWR<TrialLimit>(`/hr/people/${empId}/contract-limits?category=${category}&end_date=${endDate}`, fetcher);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const body = { salary_eur: salary, weekly_hours: hours, trial, ...(cdi ? { category } : { end_date: endDate, cdd_reason: reason }) };
    try {
      if (existing) await api(`/hr/contracts/${existing.id}`, { method: "PATCH", body });
      else await api(`/hr/people/${empId}/contract`, { body });
      await mutate(() => true);
      onClose();
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title={existing ? "Change the contract draft" : "Draft the contract"} width="max-w-xl">
      <form onSubmit={save} className="space-y-4">
        <p className="text-ink-soft">{contractType} starting {day(startDate)} {startDate.slice(0, 4)}. The job title, contract type and start date come from the person's record.</p>
        {cdi ? (
          <label className="block"><span className="label">Category</span>
            <select className="field" required value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Choose…</option>
              {Object.entries(CATEGORY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        ) : (
          <>
            <label className="block"><span className="label">End date</span><input className="field" type="date" required value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
            <label className="block"><span className="label">Reason for the fixed-term contract</span>
              <input className="field" required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: temporary increase in activity" />
              <span className="mt-1 block text-sm text-ink-soft">It must be stated precisely (Code du travail, L1242-12).</span></label>
          </>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block"><span className="label">Gross monthly salary (€)</span><input className="field" inputMode="decimal" required value={salary} onChange={(e) => setSalary(e.target.value)} /></label>
          <label className="block"><span className="label">Hours per week</span><input className="field" inputMode="decimal" required value={hours} onChange={(e) => setHours(e.target.value)} /></label>
        </div>
        <label className="block"><span className="label">Trial period ({cdi ? "months" : "days"}, 0 for none)</span>
          <input className="field" inputMode="numeric" required value={trial} onChange={(e) => setTrial(e.target.value)} />
          <span className={`mt-1 block text-sm ${limit?.error ? "text-wait" : "text-ink-soft"}`}>{limit?.error ?? limit?.explain ?? ""}</span>
        </label>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{existing ? "Save the changes" : "Write the draft"}</button>
        </div>
      </form>
    </Dialog>
  );
}

function SignedDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const [signedOn, setSignedOn] = useState(new Date().toISOString().slice(0, 10));
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try { await api(`/hr/contracts/${id}/signed`, { body: { signed_on: signedOn, note } }); await mutate(() => true); onClose(); }
    catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <Dialog open onClose={onClose} title="Mark the contract as signed">
      <form onSubmit={save} className="space-y-4">
        <p className="text-ink-soft">Do this once both parties have signed outside the app. The checklist's contract step is marked done.</p>
        <label className="block"><span className="label">Signed on</span><input className="field" type="date" required value={signedOn} onChange={(e) => setSignedOn(e.target.value)} /></label>
        <label className="block"><span className="label">Note (optional)</span><input className="field" value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: both copies received by post" /></label>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2"><button type="button" className="btn-quiet" onClick={onClose}>Not yet</button><button className="btn-primary" disabled={busy}>Mark as signed</button></div>
      </form>
    </Dialog>
  );
}
