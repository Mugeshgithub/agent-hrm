"use client";
// Everything waiting on a decision from this manager or HR person, in one place. Leave, paid leave and
// floating holidays are decided right here (the same card and rules as Approvals); HR requests are
// answered in the Request inbox. Their own requests aren't here: those go to their manager.
import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { relative, timestamp } from "@/lib/format";
import { NeedsYou } from "@/lib/types";
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
