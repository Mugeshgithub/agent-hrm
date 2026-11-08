"use client";
// The checklist of a new hire, on their profile (HR). The rules (who may change which step, a reason to
// skip or reopen) are in onboarding.py; this only shows the steps and sends the click.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { day, timestamp } from "@/lib/format";
import { HrDocument, Onboarding, OnboardingStep, StepOwner } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";
import { TextDialog } from "@/components/TextDialog";
import { WelcomeLinkPanel } from "@/components/WelcomeLinkPanel";

export const OWNER: Record<StepOwner, string> = { new_hire: "New hire", hr: "HR", manager: "Manager" };

// a small coloured dot and a word, like every other status in the app
function StepStatus({ s }: { s: OnboardingStep }) {
  const [color, label] = s.status === "done" ? ["bg-ok", "Done"] : s.status === "skipped" ? ["bg-off", "Skipped"]
    : s.overdue ? ["bg-no", "Overdue"] : ["bg-wait", "To do"];
  return (
    <span className="inline-flex items-center gap-1.5 text-sm whitespace-nowrap">
      <span className={`h-2 w-2 rounded-full ${color}`} aria-hidden />{label}
    </span>
  );
}

export function OnboardingChecklist({ data }: { data: Onboarding }) {
  const [asking, setAsking] = useState<{ step: OnboardingStep; action: "skip" | "reopen" } | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const { data: documents } = useSWR<HrDocument[]>(`/hr/people/${data.emp_id}/documents`, fetcher);
  const docOf = (key: string) => documents?.find((d) => d.step_key === key);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const { done, skipped, total } = data.progress;
  const handled = done + skipped;

  async function complete(step: OnboardingStep) {
    setBusy(step.id);
    setError("");
    try {
      await api(`/onboarding/steps/${step.id}/complete`, { body: {} });
      mutate(() => true);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }

  return (
    <section aria-labelledby="onboarding" className="panel">
      <div className="px-5 pt-5">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="onboarding" className="font-medium">Onboarding</h2>
          <p className="text-sm text-ink-soft">{handled} of {total} steps {data.progress.complete ? "handled" : "handled so far"}</p>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-rule" role="progressbar" aria-valuemin={0} aria-valuemax={total}
             aria-valuenow={handled} aria-label="Onboarding progress">
          <div className="h-full bg-dossier" style={{ width: `${(handled / total) * 100}%` }} />
        </div>
        <p className="mt-2 text-sm text-ink-soft">
          Starts {day(data.start_date)} {data.start_date.slice(0, 4)}. Steps for the new hire can be marked done here when HR has what is needed.
          {" "}<button className="text-no hover:underline" onClick={() => setCancelling(true)}>Cancel this hire</button>
        </p>
      </div>
      <WelcomeLinkPanel empId={data.emp_id} firstName={data.name.split(" ")[0]} />
      {error && <div className="px-5 pt-3"><ErrorNote message={error} /></div>}
      <ol className="mt-4 divide-y divide-rule border-t border-rule">
        {data.steps.map((s) => (
          <li key={s.id} className="flex flex-col gap-2 px-5 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
            <div className="min-w-0">
              <p className={s.status === "done" ? "font-medium text-ink-soft" : "font-medium"}>{s.title}</p>
              <p className="text-sm text-ink-soft">{s.detail}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-ink-soft">
                <StepStatus s={s} />
                <span>{s.owner === "manager" && data.manager_name ? `Manager (${data.manager_name})` : OWNER[s.owner]}</span>
                {s.status === "todo"
                  ? <span>Due {day(s.due_date)}</span>
                  : <span>{s.status === "done" ? "Done" : "Skipped"} by {s.done_by_name ?? "someone"}{s.note ? `: ${s.note}` : ""}</span>}
              </p>
              {docOf(s.step_key) && <HrDocumentInfo doc={docOf(s.step_key)!} />}
            </div>
            <div className="flex shrink-0 gap-2">
              {s.status === "todo" ? (
                <>
                  <button className="btn-ghost" onClick={() => setAsking({ step: s, action: "skip" })}>Skip</button>
                  <button className="btn-quiet" disabled={busy === s.id} onClick={() => complete(s)}>Mark done</button>
                </>
              ) : (
                <button className="btn-ghost" onClick={() => setAsking({ step: s, action: "reopen" })}>Reopen</button>
              )}
            </div>
          </li>
        ))}
      </ol>
      {asking && <ReasonDialog step={asking.step} action={asking.action} onClose={() => setAsking(null)} />}
      {cancelling && (
        <TextDialog title={`Cancel ${data.name}'s hire`} danger submit="Cancel this hire" field="reason" endpoint={`/hr/people/${data.emp_id}/cancel-hire`}
                    intro="They lose access, the welcome link stops working, and every file they sent is deleted for good, together with what was read from it. This can't be undone."
                    placeholder="For example: accepted another offer" onClose={() => setCancelling(false)} />
      )}
    </section>
  );
}

// a document the new hire sent: what the assistant read, who opened the file, and anything that looked off
function HrDocumentInfo({ doc }: { doc: HrDocument }) {
  const [reviewing, setReviewing] = useState(false);
  const read = Object.entries(doc.read).map(([k, v]) => `${k}: ${v}`).join(" · ");
  return (
    <div className="mt-2 rounded border border-rule bg-paper px-3 py-2 text-sm">
      <p>{doc.status === "confirmed" ? `Confirmed by the new hire, ${timestamp(doc.confirmed_at ?? doc.uploaded_at)}.` : "Waiting for the new hire to confirm."} Read by the assistant:</p>
      <p className="break-words text-ink-soft">{read}</p>
      {doc.flagged_reason && (doc.flag_reviewed_at ? (
        <p className="mt-1 text-ink-soft">Something looked off ({doc.flagged_reason}). Looked at by {doc.flag_reviewed_by}.</p>
      ) : (
        <p className="mt-1 text-wait">Something looked off: {doc.flagged_reason} A person should look at the file.{" "}
          <button className="text-dossier hover:underline" onClick={() => setReviewing(true)}>Mark as looked at</button></p>
      ))}
      <p className="mt-1 text-ink-soft">
        <a href={`/api/hr/documents/${doc.id}/file`} target="_blank" rel="noreferrer" className="text-dossier hover:underline">Open the file</a>
        {" · "}{doc.views.length ? `Opened ${doc.views.length === 5 ? "at least " : ""}${doc.views.length} ${doc.views.length === 1 ? "time" : "times"}, last by ${doc.views[0].viewer_name ?? "someone"} on ${timestamp(doc.views[0].viewed_at)}` : "Not opened yet"}
        . Every opening is logged.
      </p>
      {reviewing && (
        <TextDialog title="Mark as looked at" label="What you found" field="note" submit="Save" endpoint={`/hr/documents/${doc.id}/reviewed`}
                    intro="Only do this once a person has opened the file and looked at what seemed off."
                    placeholder="For example: compared with the original, it is fine" onClose={() => setReviewing(false)} />
      )}
    </div>
  );
}

// skipping or reopening a step needs a reason; it goes in the change history with the HR person's name
function ReasonDialog({ step, action, onClose }: { step: OnboardingStep; action: "skip" | "reopen"; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/onboarding/steps/${step.id}/${action}`, { body: { reason } });
      mutate(() => true);
      onClose();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title={action === "skip" ? `Skip "${step.title}"` : `Reopen "${step.title}"`}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-ink-soft">
          {action === "skip" ? "Use this when the step doesn't apply to this person." : "The step goes back to the to-do list."}
        </p>
        <label className="block">
          <span className="label">Reason</span>
          <input className="field" required value={reason} onChange={(e) => setReason(e.target.value)}
                 placeholder={action === "skip" ? "For example: uses their own laptop" : "For example: wrong document"} />
          <span className="mt-1 block text-sm text-ink-soft">Saved in the change history with your name.</span>
        </label>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !reason.trim()}>{action === "skip" ? "Skip step" : "Reopen step"}</button>
        </div>
      </form>
    </Dialog>
  );
}
