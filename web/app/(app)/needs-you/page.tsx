"use client";
// Everything waiting on a decision from this manager or HR person, in one place. Leave, paid leave and
// floating holidays are decided right here (the same card and rules as Approvals); HR requests are
// answered in the Request inbox. Their own requests aren't here: those go to their manager.
import Link from "next/link";
import useSWR from "swr";
import { useState } from "react";
import { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { day, relative, timestamp } from "@/lib/format";
import { ContractApproval, DismissedGap, DocumentFlag, NeedsYou, PolicyGap, WaitingStep } from "@/lib/types";
import { PreviewText } from "@/components/ContractPanel";
import { TextDialog } from "@/components/TextDialog";
import { Avatar } from "@/components/Shell";
import { LeaveDecision } from "@/components/LeaveDecision";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

export default function NeedsYouPage() {
  const me = useMe();
  const allowed = me.role === "manager" || me.role === "hr";
  const { data, error } = useSWR<NeedsYou>(allowed ? "/needs-you" : null, fetcher);
  if (!allowed) return <Empty title="Needs you is for managers and HR">Your requests go to your manager, {me.manager_name}.</Empty>;

  const warnings = data?.paid_leave_warnings ?? [];
  return (
    <>
      <PageHeader title="Needs you"
        description="Everything waiting on a decision from you, oldest first. Your own requests aren't here: they go to your manager." />
      {error && <ErrorNote message={error.message} />}

      {warnings.length > 0 && (
        <section aria-labelledby="warn" className="panel mb-6 px-5 py-4">
          <h2 id="warn" className="font-medium">Worth a look</h2>
          <ul className="mt-2 space-y-1.5">
            {warnings.map((w) => (
              <li key={w.employee_id} className="flex items-start gap-2.5 text-sm">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-wait" aria-hidden />
                <span>{w.message} <Link href={`/people/${w.employee_id}`} className="text-dossier hover:underline">Open {w.name.split(" ")[0]}'s profile</Link></span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && data.waiting_on_you === 0 && (
        <div className="panel"><Empty title="You're all caught up">Requests from your team will appear here.</Empty></div>
      )}

      {!!data?.leave_requests.length && (
        <section aria-labelledby="leave" className="mb-6">
          <h2 id="leave" className="mb-3 font-medium">Leave requests <span className="text-ink-soft font-normal">({data.leave_requests.length})</span></h2>
          <ul className="space-y-4">
            {data.leave_requests.map((p) => <LeaveDecision key={p.request_id} p={p} />)}
          </ul>
        </section>
      )}

      {!!data?.onboarding_steps?.length && (
        <section aria-labelledby="onboarding" className="mb-6">
          <h2 id="onboarding" className="mb-3 font-medium">Onboarding steps <span className="text-ink-soft font-normal">({data.onboarding_steps.length})</span></h2>
          <ul className="panel divide-y divide-rule">
            {data.onboarding_steps.map((s) => <OnboardingStepRow key={s.step_id} s={s} isHR={me.role === "hr"} />)}
          </ul>
        </section>
      )}

      {!!data?.new_applications?.length && (
        <section aria-labelledby="applications" className="mb-6">
          <h2 id="applications" className="mb-1 font-medium">New applications <span className="text-ink-soft font-normal">({data.new_applications.length})</span></h2>
          <p className="mb-3 text-sm text-ink-soft">Someone has to look at each one and move it to the next stage. The app never rejects, ranks or scores anyone.</p>
          <ul className="panel divide-y divide-rule">
            {data.new_applications.map((a) => (
              <li key={a.application_id} className="flex items-center justify-between gap-4 px-5 py-4">
                <div className="flex items-center gap-3">
                  <Avatar name={a.name} size={32} />
                  <div>
                    <p className="font-medium">{a.name}</p>
                    <p className="text-sm text-ink-soft">Applied for {a.job_title}, {timestamp(a.applied_at)}</p>
                  </div>
                </div>
                <Link href={`/recruitment/${a.job_id}`} className="btn-quiet shrink-0">Open</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!data?.contract_approvals?.length && (
        <section aria-labelledby="contracts" className="mb-6">
          <h2 id="contracts" className="mb-3 font-medium">Contracts to approve <span className="text-ink-soft font-normal">({data.contract_approvals.length})</span></h2>
          <ul className="space-y-4">{data.contract_approvals.map((c) => <ContractApprovalCard key={c.draft_id} c={c} />)}</ul>
        </section>
      )}

      {!!data?.document_flags?.length && (
        <section aria-labelledby="docs" className="mb-6">
          <h2 id="docs" className="mb-3 font-medium">Documents to look at <span className="text-ink-soft font-normal">({data.document_flags.length})</span></h2>
          <ul className="panel divide-y divide-rule">
            {data.document_flags.map((f) => <DocumentFlagRow key={f.document_id} f={f} />)}
          </ul>
        </section>
      )}

      {!!data?.hr_requests.length && (
        <section aria-labelledby="hr" className="mb-6">
          <h2 id="hr" className="mb-3 font-medium">HR requests <span className="text-ink-soft font-normal">({data.hr_requests.length})</span></h2>
          <ul className="panel divide-y divide-rule">
            {data.hr_requests.map((t) => (
              <li key={t.id} className="flex items-start justify-between gap-4 px-5 py-4">
                <div className="flex items-start gap-3">
                  <Avatar name={t.employee_name} size={32} />
                  <div>
                    <p className="font-medium">{t.employee_name} <span className="font-normal text-ink-soft">{t.ticket}</span></p>
                    <p>{t.summary}</p>
                    <p className="text-sm text-ink-soft">Sent {timestamp(t.created_at)}</p>
                  </div>
                </div>
                <Link href="/inbox" className="btn-quiet shrink-0">Reply</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!data?.policy_gaps?.length && (
        <section aria-labelledby="gaps" className="mb-6">
          <h2 id="gaps" className="mb-1 font-medium">Questions the policy doesn't answer <span className="text-ink-soft font-normal">({data.policy_gaps.length})</span></h2>
          <p className="mb-3 text-sm text-ink-soft">
            Most asked first. Edit the policy, then mark it fixed: it comes back if people still hear "the policy doesn't say".
            Dismiss it if the policy doesn't need to cover it: questions like it stop showing up.
          </p>
          <ul className="panel divide-y divide-rule">
            {data.policy_gaps.map((g) => <PolicyGapRow key={g.id} g={g} />)}
          </ul>
        </section>
      )}

      {me.role === "hr" && <DismissedGaps />}
    </>
  );
}

// the questions HR dismissed ("the policy need not cover it"), kept so a dismissal can be undone. Nothing is shown when there are none.
function DismissedGaps() {
  const { data } = useSWR<DismissedGap[]>("/hr/policy-gaps/dismissed", fetcher);
  if (!data?.length) return null;
  return (
    <section aria-labelledby="dismissed" className="mb-6">
      <details>
        <summary id="dismissed" className="cursor-pointer font-medium">Dismissed questions <span className="font-normal text-ink-soft">({data.length})</span></summary>
        <p className="mb-3 mt-1 text-sm text-ink-soft">
          Questions you decided the policy doesn't need to cover, so they stopped showing up. Restore one to see it again above.
        </p>
        <ul className="panel divide-y divide-rule">
          {data.map((g) => <DismissedGapRow key={g.id} g={g} />)}
        </ul>
      </details>
    </section>
  );
}

function DismissedGapRow({ g }: { g: DismissedGap }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function restore() {
    setBusy(true);
    setError("");
    try {
      await api(`/hr/policy-gaps/${g.id}/restore`, { body: {} });
      mutate(() => true);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <p className="font-medium break-words">{g.topic}</p>
        <p className="text-sm">{g.asks === 1 ? "Asked once" : `Asked ${g.asks} times`} before you dismissed it.</p>
        <p className="text-sm text-ink-soft">
          Dismissed by {g.dismissed_by ?? "HR"}{g.dismissed_at && `, ${timestamp(g.dismissed_at)}`}.{g.note && <> &ldquo;{g.note}&rdquo;</>}
        </p>
        {error && <p role="alert" className="mt-1 text-sm text-no">{error}</p>}
      </div>
      <div className="flex shrink-0 gap-2">
        <button className="btn-quiet" disabled={busy} onClick={restore}>Restore</button>
      </div>
    </li>
  );
}

// a question people asked that the policy does not answer. Nobody is named: only the wording and how many times it was asked are kept.
function PolicyGapRow({ g }: { g: PolicyGap }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function close(how: "fixed" | "dismiss") {
    setBusy(true);
    setError("");
    try {
      await api(`/hr/policy-gaps/${g.id}/${how}`, { body: {} });
      mutate(() => true);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  const alsoAskedAs = g.examples.slice(1);
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <p className="font-medium break-words">{g.topic}</p>
        <p className="text-sm">{g.asks === 1 ? "Asked once" : `Asked ${g.asks} times`}. The policy doesn't say.</p>
        {alsoAskedAs.length > 0 && <p className="text-sm text-ink-soft break-words">Also asked as: {alsoAskedAs.map((e) => `“${e}”`).join(", ")}</p>}
        <p className="text-sm text-ink-soft">
          Last asked {timestamp(g.last_asked_at)}.{g.reopened && " You marked it fixed before, and people still hear \"the policy doesn't say\"."}
        </p>
        {error && <p role="alert" className="mt-1 text-sm text-no">{error}</p>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Link href={`/handbook?gap=${g.id}`} className="btn-quiet">Edit the policy</Link>
        <button className="btn-quiet" disabled={busy} onClick={() => close("dismiss")}>Dismiss</button>
        <button className="btn-primary" disabled={busy} onClick={() => close("fixed")}>Mark fixed</button>
      </div>
    </li>
  );
}

// one onboarding step that waits on this person; the rules (who may mark it done) are in onboarding.py
function OnboardingStepRow({ s, isHR }: { s: WaitingStep; isHR: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function done() {
    setBusy(true);
    setError("");
    try {
      await api(`/onboarding/steps/${s.step_id}/complete`, { body: {} });
      mutate(() => true);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="flex items-start gap-3">
        <Avatar name={s.employee_name} size={32} />
        <div>
          <p className="font-medium">{s.step_title}</p>
          <p className="text-sm text-ink-soft">{s.detail}</p>
          <p className="mt-0.5 text-sm text-ink-soft">
            <Link href={`/people/${s.employee_id}`} className="text-dossier hover:underline">{s.employee_name}</Link>, {s.job_title}, starts {day(s.start_date)}
            {" · "}
            {s.overdue ? <span className="text-no font-medium">Overdue, was due {day(s.due_date)}</span> : <>Due {day(s.due_date)} ({relative(s.due_date)})</>}
          </p>
          {error && <p role="alert" className="mt-1 text-sm text-no">{error}</p>}
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        {isHR && <Link href={`/people/${s.employee_id}`} className="btn-ghost">Open profile</Link>}
        <button className="btn-primary" disabled={busy} onClick={done}>Mark done</button>
      </div>
    </li>
  );
}

// a document something looked off about: the assistant only points at it, a person at HR decides
function DocumentFlagRow({ f }: { f: DocumentFlag }) {
  const [reviewing, setReviewing] = useState(false);
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="flex items-start gap-3">
        <Avatar name={f.employee_name} size={32} />
        <div>
          <p className="font-medium">{f.employee_name}'s {f.label}</p>
          <p className="text-sm text-wait">Something looked off: {f.reason}</p>
          <p className="text-sm text-ink-soft">The assistant can't tell whether a document is genuine. Open the file and look.</p>
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        <Link href={`/people/${f.employee_id}`} className="btn-quiet">Open profile</Link>
        <button className="btn-primary" onClick={() => setReviewing(true)}>Mark as looked at</button>
      </div>
      {reviewing && (
        <TextDialog title="Mark as looked at" label="What you found" field="note" submit="Save" endpoint={`/hr/documents/${f.document_id}/reviewed`}
                    intro="Only do this once a person has opened the file and looked at what seemed off."
                    placeholder="For example: compared with the original, it is fine" onClose={() => setReviewing(false)} />
      )}
    </li>
  );
}

// a contract written by HR for someone on this manager's team: read it, then approve or reject (a reason is needed to reject)
function ContractApprovalCard({ c }: { c: ContractApproval }) {
  const [reading, setReading] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function decide(decision: "approve" | "reject") {
    setBusy(true);
    setError("");
    try { await api(`/contracts/${c.draft_id}/decision`, { body: { decision, reason: decision === "reject" ? reason.trim() : "" } }); mutate(() => true); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return (
    <li className="panel">
      <div className="grid gap-4 p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-start">
        <div className="flex items-start gap-3">
          <Avatar name={c.employee_name} size={36} />
          <div>
            <p className="font-medium">{c.employee_name}'s {c.contract_type} contract</p>
            <p>{c.job_title}, starts {day(c.start_date)}</p>
            <p className="text-sm text-ink-soft">Written by {c.submitted_by}</p>
          </div>
        </div>
        <div className="space-y-1 text-sm">
          <p><span className="text-ink-soft">Gross salary </span><span className="font-medium">{c.salary_eur.toLocaleString("fr-FR", { style: "currency", currency: "EUR" })} a month</span>, {c.weekly_hours} hours a week</p>
          <p><span className="text-ink-soft">Trial period </span><span className="font-medium">{c.trial_text}</span>, checked against the law</p>
          <button className="text-dossier hover:underline" onClick={() => setReading((r) => !r)}>{reading ? "Hide the contract" : "Read the contract"}</button>
        </div>
        {!rejecting && (
          <div className="flex gap-2 md:justify-end">
            <button className="btn-quiet" disabled={busy} onClick={() => setRejecting(true)}>Reject</button>
            <button className="btn-primary" disabled={busy || c.stale} onClick={() => decide("approve")}>Approve</button>
          </div>
        )}
      </div>
      {c.stale && <p className="px-5 pb-3 text-sm text-no">The person's details changed after this was sent. HR has to update it first.</p>}
      {reading && <div className="px-5 pb-4"><PreviewText id={c.draft_id} /></div>}
      {rejecting && (
        <div className="border-t border-rule px-5 py-4">
          <label className="block"><span className="label">What should HR change?</span>
            <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: the salary should be 3,400" autoFocus /></label>
          <div className="mt-3 flex gap-2">
            <button className="btn-danger" disabled={busy || !reason.trim()} onClick={() => decide("reject")}>Reject the contract</button>
            <button className="btn-ghost" onClick={() => { setRejecting(false); setReason(""); }}>Back</button>
          </div>
        </div>
      )}
      {error && <div className="px-5 pb-4"><p role="alert" className="text-sm text-no">{error}</p></div>}
    </li>
  );
}
