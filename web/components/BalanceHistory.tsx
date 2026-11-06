"use client";
// why a balance is what it is: HR adjustments and the yearly reset, newest first
import { leaveLabel, timestamp } from "@/lib/format";
import { BalanceHistory as History } from "@/lib/types";

export function BalanceHistory({ data, empty }: { data?: History; empty: string }) {
  if (!data) return <p className="px-5 py-5 text-ink-soft">Loading…</p>;
  if (data.adjustments.length === 0) return <p className="px-5 py-5 text-ink-soft">{empty}</p>;
  return (
    <ol className="divide-y divide-rule">
      {data.adjustments.map((a, i) => (
        <li key={i} className="flex items-start justify-between gap-4 px-5 py-3">
          <div>
            <p>{a.reason}</p>
            <p className="text-sm text-ink-soft">{leaveLabel(a.leave_type)}, {a.changed_by}, {timestamp(a.changed_at)}</p>
          </div>
          <span className={`shrink-0 font-semibold tabular-nums ${a.days > 0 ? "text-ok" : "text-no"}`}>
            {a.days > 0 ? "+" : "−"}{Math.abs(a.days)} {Math.abs(a.days) === 1 ? "day" : "days"}
          </span>
        </li>
      ))}
    </ol>
  );
}
