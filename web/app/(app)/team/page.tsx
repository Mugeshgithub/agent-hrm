"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { toISO } from "@/lib/format";
import { Balances, CalendarLeave, PENDING, Person } from "@/lib/types";
import { Avatar } from "@/components/Shell";
import { DayTiles, TileLegend } from "@/components/DayTiles";
import { Empty, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

type Member = Person & { balances: Balances };
const DAYS = 21;
const WD = ["S", "M", "T", "W", "T", "F", "S"];

export default function Team() {
  const me = useMe();
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const days = Array.from({ length: DAYS }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
  const from = toISO(days[0]), to = toISO(days[DAYS - 1]);
  const { data: team } = useSWR<Member[]>(me.role === "manager" ? "/team" : null, fetcher);
  const { data: cal } = useSWR<{ leave: CalendarLeave[]; holidays: string[] }>(me.role === "manager" ? `/calendar?start=${from}&end=${to}` : null, fetcher);
  if (me.role !== "manager") return <Empty title="Team view is for managers" />;

  return (
    <>
      <PageHeader title="Team" description={`${team?.length ?? ""} people report to you. The strip shows the next three weeks.`} />

      <section aria-label="Who's off" className="panel overflow-x-auto mb-6">
        <table className="w-full min-w-[720px] table-fixed border-collapse">
          <thead>
            <tr>
              <th className="table-head sticky left-0 bg-surface px-5 py-3 w-40">Person</th>
              {days.map((d) => {
                const weekend = d.getDay() === 0 || d.getDay() === 6;
                return (
                  <th key={d.toISOString()} className={`px-0 py-2 text-center text-xs font-normal ${weekend ? "text-ink-faint" : "text-ink-soft"}`}>
                    <span className="block">{WD[d.getDay()]}</span>
                    <span className={`block ${toISO(d) === from ? "font-semibold text-ink" : ""}`}>{d.getDate()}</span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {team?.map((m) => (
              <tr key={m.id} className="border-t border-rule">
                <td className="sticky left-0 bg-surface px-5 py-2.5">
                  <span className="flex items-center gap-2"><Avatar name={m.name} size={24} /><span className="font-medium">{m.name}</span></span>
                </td>
                {days.map((d) => {
                  const iso = toISO(d);
                  const weekend = d.getDay() === 0 || d.getDay() === 6;
                  const holiday = cal?.holidays.includes(iso);
                  const l = cal?.leave.find((x) => x.employee_id === m.id && x.start_date <= iso && x.end_date >= iso);
                  const off = l && !weekend && !holiday;
                  return (
                    <td key={iso} className={`p-[3px] ${weekend || holiday ? "bg-paper" : ""}`}
                        title={off ? `${m.name}: ${l!.status === PENDING ? "waiting for approval" : "approved"}` : holiday ? "Public holiday" : undefined}>
                      <span className={`block h-6 rounded-[3px] ${off ? (l!.status === PENDING ? "tile-pending" : "bg-dossier") : ""}`} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="border-t border-rule px-5 py-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft">
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[2px] bg-dossier" />Approved</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[2px] tile-pending" />Waiting for your decision</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[2px] bg-paper border border-rule" />Weekend or public holiday</span>
        </div>
      </section>

      <section aria-labelledby="bal" className="panel">
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-3 border-b border-rule">
          <h2 id="bal" className="font-medium">Balances</h2>
          <TileLegend />
        </div>
        <ul className="divide-y divide-rule">
          {team?.map((m) => (
            <li key={m.id} className="grid gap-3 px-5 py-4 md:grid-cols-[220px_1fr_1fr_1fr] md:items-center">
              <div>
                <p className="font-medium">{m.name}</p>
                <p className="text-sm text-ink-soft">{m.title}, {m.location}</p>
              </div>
              <div><p className="text-sm text-ink-soft mb-1.5">Casual, {m.balances.casual.available} available</p><DayTiles size="sm" balance={m.balances.casual} /></div>
              <div><p className="text-sm text-ink-soft mb-1.5">Sick, {m.balances.sick.available} available</p><DayTiles size="sm" balance={m.balances.sick} /></div>
              <div><p className="text-sm text-ink-soft mb-1.5">Paid, {m.balances.paid.available} available</p><DayTiles size="sm" balance={m.balances.paid} /></div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
