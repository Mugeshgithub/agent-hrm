"use client";
// One request waiting on a manager, with one-click Approve and Reject (a reason is required to reject).
// Used by the Approvals page and the Needs you inbox, so both follow the same rules.
import { useState } from "react";
import { mutate } from "swr";
import { api } from "@/lib/api";
import { leaveLabel, range, relative } from "@/lib/format";
import { Balances, Pending } from "@/lib/types";
import { Avatar } from "@/components/Shell";
import { ErrorNote } from "@/components/PageHeader";

export function LeaveDecision({ p }: { p: Pending }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const after = p.balances[p.leave_type as keyof Balances] as Pending["balances"]["casual"] | undefined;   // undefined for floating holidays

  async function decide(decision: "approve" | "reject") {
    setBusy(true);
    setError("");
    try {
      await api(`/approvals/${p.request_id}`, { body: { decision, reason: decision === "reject" ? reason.trim() : "" } });
      mutate(() => true);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return (
    <li className="panel">
      <div className="grid gap-4 p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-start">
        <div className="flex items-start gap-3">
          <Avatar name={p.employee_name} size={36} />
          <div>
            <p className="font-medium">{p.employee_name}</p>
            <p>{leaveLabel(p.leave_type)}, {range(p.start, p.end)}</p>
            <p className="text-sm text-ink-soft">{p.days} {p.leave_type === "paid" ? "" : "working "}{p.days === 1 ? "day" : "days"}, starts {relative(p.start)}</p>
          </div>
        </div>
        <div className="text-sm space-y-1">
          {after ? (
          <p><span className="text-ink-soft">{leaveLabel(p.leave_type)} balance </span><span className="font-medium">{after.balance} {after.balance === 1 ? "day" : "days"}</span>
            {after.pending > p.days
              ? <span className="text-ink-soft">; {after.available} left if all {after.pending} waiting days are approved</span>
              : <span className="text-ink-soft">; {after.balance - p.days} left after this one</span>}</p>
          ) : <p className="text-ink-soft">A floating holiday from their holiday plan. It doesn't use their leave balance.</p>}
          {p.overlaps.length === 0 ? <p className="text-ink-soft">No one else on the team is off then.</p> : (
            <p><span className="text-wait font-medium">Also off: </span>
              {p.overlaps.map((o) => `${o.name} (${range(o.start_date, o.end_date)})`).join(", ")}</p>
          )}
        </div>
        {!rejecting && (
          <div className="flex gap-2 md:justify-end">
            <button className="btn-quiet" disabled={busy} onClick={() => setRejecting(true)}>Reject</button>
            <button className="btn-primary" disabled={busy} onClick={() => decide("approve")}>Approve</button>
          </div>
        )}
      </div>
      {rejecting && (
        <div className="border-t border-rule px-5 py-4">
          <label className="block"><span className="label">Reason for {p.employee_name}</span>
            <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus
                   placeholder="For example: release week, could you move it by a few days?" /></label>
          <p className="mt-1 text-sm text-ink-soft">A reason is required. It helps them plan or ask again.</p>
          <div className="mt-3 flex gap-2">
            <button className="btn-danger" disabled={!reason.trim() || busy} onClick={() => decide("reject")}>Reject request</button>
            <button className="btn-ghost" onClick={() => setRejecting(false)}>Back</button>
          </div>
        </div>
      )}
      {error && <div className="px-5 pb-4"><ErrorNote message={error} /></div>}
    </li>
  );
}
