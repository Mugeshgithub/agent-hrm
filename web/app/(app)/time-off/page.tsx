"use client";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { daysUntil, leaveLabel, range } from "@/lib/format";
import { BalanceHistory as History, Balances, CalendarLeave, LeaveRequest, PENDING } from "@/lib/types";
import { BalanceHistory } from "@/components/BalanceHistory";
import { DayTiles, TileLegend } from "@/components/DayTiles";
import { LeaveForm } from "@/components/LeaveForm";
import { MonthCalendar, monthBounds } from "@/components/MonthCalendar";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { Status } from "@/components/Status";
import { Dialog } from "@/components/Dialog";

type Overview = { balances: Balances; requests: LeaveRequest[]; holidays: string[] };

export default function TimeOff() {
  const { data } = useSWR<Overview>("/leave", fetcher);
  const { data: history } = useSWR<History>("/leave/history", fetcher);
  const [month, setMonth] = useState(() => new Date());
  const { start, end } = monthBounds(month);
  const { data: cal } = useSWR<{ leave: CalendarLeave[]; holidays: string[] }>(`/calendar?start=${start}&end=${end}`, fetcher);
  const [asking, setAsking] = useState(false);
  const [withdraw, setWithdraw] = useState<LeaveRequest | null>(null);
  const [error, setError] = useState("");

  async function confirmWithdraw() {
    if (!withdraw) return;
    try {
      await api(`/leave/${withdraw.id}/cancel`, { body: {} });
      setWithdraw(null);
      mutate(() => true);
    } catch (e) { setError((e as Error).message); }
  }

  const booked = (t: string) => (data?.requests ?? []).filter((r) => r.leave_type === t && r.status === "approved" && daysUntil(r.end_date) >= 0).reduce((s, r) => s + r.days, 0);
  const mine = (cal?.leave ?? []).filter((l) => data?.requests.some((r) => r.id === l.id));

  return (
    <>
      <PageHeader title="Time off" description="Your balances, requests and calendar. Days waiting for approval already count against what you can request."
                  actions={<button className="btn-primary" onClick={() => setAsking(true)}>Request time off</button>} />

      {data && (
        <section aria-label="Balances" className="panel mb-6">
          <div className="grid sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-rule">
          {(["casual", "sick"] as const).map((t) => (
            <div key={t} className="p-5">
              <div className="flex items-baseline justify-between">
                <p className="font-medium">{leaveLabel(t)}</p>
                <p className="text-sm text-ink-soft">{data.balances[t].balance} on balance, {data.balances[t].pending} waiting</p>
              </div>
              <p className="mt-1"><span className="text-xl font-semibold">{data.balances[t].available}</span><span className="ml-1.5 text-ink-soft">available to request</span></p>
              <div className="mt-3"><DayTiles balance={data.balances[t]} booked={booked(t)} /></div>
            </div>
          ))}
          </div>
          <div className="border-t border-rule px-5 py-3"><TileLegend /></div>
        </section>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section aria-label="Calendar" className="panel overflow-hidden">
          <MonthCalendar month={month} onMonth={setMonth} leave={mine} holidays={cal?.holidays ?? []} />
        </section>

        <section aria-labelledby="reqs" className="panel">
          <h2 id="reqs" className="px-5 pt-4 pb-3 font-medium border-b border-rule">Your requests</h2>
          {!data?.requests.length ? <Empty title="No requests yet">Use "Request time off" to send your first one.</Empty> : (
            <ul className="divide-y divide-rule">
              {data.requests.map((r) => {
                const canWithdraw = r.status === PENDING || (r.status === "approved" && daysUntil(r.start_date) > 0);
                return (
                  <li key={r.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium">{range(r.start_date, r.end_date)}</p>
                        <p className="text-sm text-ink-soft">{leaveLabel(r.leave_type)}, {r.days} {r.days === 1 ? "day" : "days"}</p>
                      </div>
                      <Status value={r.status} />
                    </div>
                    {r.reason && <p className="mt-1.5 text-sm text-ink-soft">Reason: {r.reason}</p>}
                    {canWithdraw && <button className="mt-1.5 text-sm text-no hover:underline" onClick={() => { setError(""); setWithdraw(r); }}>Withdraw</button>}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      <section aria-labelledby="changes" className="panel mt-6">
        <div className="px-5 pt-4 pb-3 border-b border-rule">
          <h2 id="changes" className="font-medium">Balance changes</h2>
          <p className="text-sm text-ink-soft">Changes that aren't leave requests: HR corrections and the start-of-year update. {history?.rules}</p>
        </div>
        <BalanceHistory data={history} empty="No changes yet. Approved leave is listed in your requests above." />
      </section>

      <LeaveForm open={asking} onClose={() => setAsking(false)} />
      <Dialog open={!!withdraw} onClose={() => setWithdraw(null)} title="Withdraw this request?">
        {withdraw && (
          <div className="space-y-4">
            <p>{leaveLabel(withdraw.leave_type)}, {range(withdraw.start_date, withdraw.end_date)}.
              {withdraw.status === "approved" ? ` It's already approved, so ${withdraw.days} ${withdraw.days === 1 ? "day goes" : "days go"} back to your balance.` : " Your manager won't need to decide on it any more."}</p>
            {error && <ErrorNote message={error} />}
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setWithdraw(null)}>Keep it</button>
              <button className="btn-danger" onClick={confirmWithdraw}>Withdraw request</button>
            </div>
          </div>
        )}
      </Dialog>
    </>
  );
}
