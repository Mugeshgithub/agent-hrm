"use client";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { CalendarLeave, PENDING } from "@/lib/types";
import { leaveLabel, toISO } from "@/lib/format";

const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function monthBounds(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const last = new Date(month.getFullYear(), month.getMonth() + 1, 0);
  return { start: toISO(first), end: toISO(last), first, last };
}

export function MonthCalendar({ month, onMonth, leave, holidays, showNames = false }: {
  month: Date; onMonth: (d: Date) => void; leave: CalendarLeave[]; holidays: string[]; showNames?: boolean;
}) {
  const { first, last } = monthBounds(month);
  const lead = (first.getDay() + 6) % 7;                       // Monday-first grid
  const cells: (Date | null)[] = [...Array(lead).fill(null)];
  for (let d = 1; d <= last.getDate(); d++) cells.push(new Date(month.getFullYear(), month.getMonth(), d));
  while (cells.length % 7) cells.push(null);
  const todayISO = toISO(new Date());

  return (
    <div>
      <div className="flex items-center justify-between px-5 py-3 border-b border-rule">
        <p className="font-medium">{MONTHS[month.getMonth()]} {month.getFullYear()}</p>
        <div className="flex gap-1">
          <button className="btn-ghost h-8 w-8 px-0" aria-label="Previous month" onClick={() => onMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft size={16} /></button>
          <button className="btn-ghost h-8 px-2 text-sm" onClick={() => onMonth(new Date())}>Today</button>
          <button className="btn-ghost h-8 w-8 px-0" aria-label="Next month" onClick={() => onMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight size={16} /></button>
        </div>
      </div>
      <div className="grid grid-cols-7 text-xs text-ink-soft border-b border-rule">
        {WEEK.map((w) => <div key={w} className="px-2 py-1.5">{w}</div>)}
      </div>
      <div className="grid grid-cols-7">
        {cells.map((d, i) => {
          const edge = (i + 1) % 7 === 0 ? "" : "border-r";
          if (!d) return <div key={i} className={`min-h-[76px] border-b ${edge} border-rule bg-paper/50`} />;
          const iso = toISO(d);
          const weekend = d.getDay() === 0 || d.getDay() === 6;
          const holiday = holidays.includes(iso);
          const items = leave.filter((l) => l.start_date <= iso && l.end_date >= iso);
          return (
            <div key={i} className={`min-h-[76px] border-b ${edge} border-rule p-1.5 ${weekend ? "bg-paper/60" : ""}`}>
              <span className={`inline-grid h-6 min-w-6 place-items-center rounded-full px-1 text-xs ${
                iso === todayISO ? "bg-ink text-white font-medium" : weekend ? "text-ink-faint" : "text-ink-soft"}`}>{d.getDate()}</span>
              {holiday && <p className="mt-0.5 truncate text-xs text-no">Public holiday</p>}
              <div className="mt-0.5 space-y-0.5">
                {!weekend && !holiday && items.map((l) => (
                  <p key={l.id} title={`${l.name}: ${leaveLabel(l.leave_type)}, ${l.status === PENDING ? "waiting for approval" : "approved"}`}
                     className={`truncate rounded-sm px-1 text-xs leading-5 ${l.status === PENDING ? "tile-pending text-ink" : "bg-dossier text-white"}`}>
                    <span className={l.status === PENDING ? "bg-[#F8EEDC] px-0.5" : ""}>
                      {showNames ? l.name : leaveLabel(l.leave_type).replace(" leave", "")}
                    </span>
                  </p>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
