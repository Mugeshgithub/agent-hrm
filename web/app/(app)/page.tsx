"use client";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { day, leaveLabel, monthName, range, readable, relative } from "@/lib/format";
import { Balances, CalendarLeave, LeaveRequest, Notification, Person, PENDING } from "@/lib/types";
import { DayTiles, TileLegend } from "@/components/DayTiles";
import { LeaveForm } from "@/components/LeaveForm";
import { Status } from "@/components/Status";
import { useMe } from "@/components/Me";

type Home = {
  person: Person; balances: Balances; upcoming_leave: LeaveRequest[]; notifications: Notification[];
  open_tickets: number; latest_payslip: { month: string; net_paid_eur: string; gross_eur: string } | null;
  holidays: string[]; pending_approvals?: number; team_size?: number; team_leave?: CalendarLeave[];
  open_hr_tickets?: number; headcount?: number;
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function HomePage() {
  const me = useMe();
  const { data } = useSWR<Home>("/home", fetcher);
  const [asking, setAsking] = useState(false);
  if (!data) return <p className="text-ink-soft">Loading…</p>;

  const booked = (type: string) => data.upcoming_leave
    .filter((r) => r.leave_type === type && r.status === "approved").reduce((s, r) => s + r.days, 0);
  const today = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });

  return (
    <>
      <div className="mb-8">
        <p className="text-ink-soft">{today}</p>
        <h1 className="mt-1 text-xl font-semibold tracking-[-0.01em]">{greeting()}, {me.name}</h1>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section aria-labelledby="timeoff" className="panel">
          <div className="flex items-center justify-between px-5 pt-5">
            <h2 id="timeoff" className="text-md font-semibold">Your time off</h2>
            <button className="btn-primary" onClick={() => setAsking(true)}>Request time off</button>
          </div>
          <div className="grid sm:grid-cols-2 gap-6 px-5 py-5">
            {(["casual", "sick"] as const).map((t) => (
              <div key={t}>
                <p className="text-sm text-ink-soft">{leaveLabel(t)}</p>
                <p className="mt-0.5"><span className="text-xl font-semibold">{data.balances[t].available}</span>
                  <span className="ml-1.5 text-ink-soft">{data.balances[t].available === 1 ? "day" : "days"} available</span></p>
                <div className="mt-3"><DayTiles balance={data.balances[t]} booked={booked(t)} /></div>
              </div>
            ))}
          </div>
          <div className="px-5 pb-4"><TileLegend /></div>

          <div className="border-t border-rule">
            <div className="flex items-center justify-between px-5 pt-4 pb-2">
              <h3 className="font-medium">Coming up</h3>
              <Link href="/time-off" className="text-sm text-dossier hover:underline">All requests</Link>
            </div>
            {data.upcoming_leave.length === 0 ? (
              <p className="px-5 pb-5 text-ink-soft">Nothing booked yet. Requests you send will show here with their status.</p>
            ) : (
              <ul className="divide-y divide-rule">
                {data.upcoming_leave.map((r) => (
                  <li key={r.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <span className="font-medium">{range(r.start_date, r.end_date)}</span>
                      <Status value={r.status} />
                    </div>
                    <p className="text-sm text-ink-soft">{leaveLabel(r.leave_type)}, {r.days} {r.days === 1 ? "day" : "days"}, {relative(r.start_date)}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <div className="space-y-6">
          {(me.role === "manager" || me.role === "hr") && (
            <section aria-labelledby="needs" className="panel p-5">
              <h2 id="needs" className="font-medium">Needs you</h2>
              {me.role === "manager" && (
                <Link href="/approvals" className="mt-3 flex items-baseline justify-between rounded border border-rule px-3 py-2.5 hover:border-dossier">
                  <span>Leave requests to decide</span>
                  <span className={`text-lg font-semibold ${data.pending_approvals ? "text-dossier" : "text-ink-faint"}`}>{data.pending_approvals}</span>
                </Link>
              )}
              {me.role === "hr" && (
                <Link href="/inbox" className="mt-3 flex items-baseline justify-between rounded border border-rule px-3 py-2.5 hover:border-dossier">
                  <span>Open HR requests</span>
                  <span className={`text-lg font-semibold ${data.open_hr_tickets ? "text-dossier" : "text-ink-faint"}`}>{data.open_hr_tickets}</span>
                </Link>
              )}
              {me.role === "manager" && (
                <div className="mt-4">
                  <p className="text-sm text-ink-soft">Team leave, next 3 weeks</p>
                  {data.team_leave?.length ? (
                    <ul className="mt-2 space-y-1.5">
                      {data.team_leave.map((l) => (
                        <li key={l.id} className="flex items-center justify-between gap-3 text-sm">
                          <span><span className="font-medium">{l.name}</span> <span className="text-ink-soft">{range(l.start_date, l.end_date)}</span></span>
                          <span className={`h-2 w-2 shrink-0 rounded-full ${l.status === PENDING ? "bg-wait" : "bg-ok"}`} title={l.status === PENDING ? "Waiting for approval" : "Approved"} />
                        </li>
                      ))}
                    </ul>
                  ) : <p className="mt-1 text-sm">Everyone's in.</p>}
                </div>
              )}
            </section>
          )}

          <section aria-label="Shortcuts" className="panel divide-y divide-rule">
            <Link href="/payslips" className="block px-5 py-4 hover:bg-paper/60">
              <p className="text-sm text-ink-soft">Latest payslip</p>
              {data.latest_payslip ? (
                <p className="mt-0.5"><span className="text-lg font-semibold">{data.latest_payslip.net_paid_eur}</span>
                  <span className="ml-2 text-ink-soft">net, {monthName(data.latest_payslip.month)}</span></p>
              ) : <p className="mt-0.5">None issued yet</p>}
            </Link>
            <Link href="/requests" className="block px-5 py-4 hover:bg-paper/60">
              <p className="text-sm text-ink-soft">Your HR requests</p>
              <p className="mt-0.5">{data.open_tickets ? `${data.open_tickets} waiting for an answer` : "None open"}</p>
            </Link>
            <div className="px-5 py-4">
              <p className="text-sm text-ink-soft">Public holidays</p>
              {data.holidays.length ? (
                <ul className="mt-1 space-y-0.5">{data.holidays.map((h) => <li key={h}>{day(h)} <span className="text-ink-soft">{relative(h)}</span></li>)}</ul>
              ) : <p className="mt-0.5">None in the calendar</p>}
            </div>
          </section>

          {data.notifications.length > 0 && (
            <section aria-labelledby="recent" className="px-1">
              <h2 id="recent" className="text-sm font-medium text-ink-soft">Recent updates</h2>
              <ul className="mt-2 space-y-2">
                {data.notifications.slice(0, 3).map((n) => <li key={n.id} className="text-sm leading-snug">{readable(n.message)}</li>)}
              </ul>
            </section>
          )}
        </div>
      </div>
      <LeaveForm open={asking} onClose={() => setAsking(false)} />
    </>
  );
}
