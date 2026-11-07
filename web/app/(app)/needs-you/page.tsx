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
import { NeedsYou, WaitingStep } from "@/lib/types";
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
    </>
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
