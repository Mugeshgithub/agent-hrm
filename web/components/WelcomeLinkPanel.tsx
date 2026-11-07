"use client";
// HR's controls for a new hire's welcome link, on their onboarding card. The rules (14 days, the 6-digit code,
// locking after 5 wrong codes, the email waiting in the outbox) are in welcome.py. The code is shown once, here:
// it is not in the email and not stored in a readable form.
import Link from "next/link";
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { day, timestamp } from "@/lib/format";
import { IssuedLink, WelcomeLinkStatus } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";

export function WelcomeLinkPanel({ empId, firstName }: { empId: string; firstName: string }) {
  const { data: status } = useSWR<WelcomeLinkStatus>(`/hr/people/${empId}/welcome-link`, fetcher);
  const [issued, setIssued] = useState<IssuedLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);

  async function create() {
    setBusy(true);
    setError("");
    try {
      setIssued(await api<IssuedLink>(`/hr/people/${empId}/welcome-link`, { body: {} }));
      await Promise.all([mutate(`/hr/people/${empId}/welcome-link`), mutate("/hr/outbox"), mutate(`/people/${empId}/history`)]);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const state = status?.state;
  const line = !status ? "Not created yet. Creating it puts the welcome email in the Outbox for you to copy."
    : state === "ready" ? `Valid until ${day(status.expires_at.slice(0, 10))}. ${status.last_used_at ? `Opened ${timestamp(status.last_used_at)}.` : "Not opened yet."}`
    : state === "locked" ? `Locked after ${status.max_wrong_codes} wrong codes. Create a new link.`
    : state === "expired" ? `Expired on ${day(status.expires_at.slice(0, 10))}. Create a new link.`
    : "Cancelled. Create a new link if the person still needs one.";

  return (
    <div className="mx-5 mt-4 rounded border border-rule px-4 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-medium">Welcome link</p>
          <p className="text-sm text-ink-soft">{line}</p>
          {status && state === "ready" && status.wrong_codes > 0 && (
            <p className="text-sm text-wait">{status.wrong_codes} wrong {status.wrong_codes === 1 ? "code" : "codes"} so far (it locks at {status.max_wrong_codes}).</p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {state === "ready" && <button className="btn-ghost" onClick={() => setCancelling(true)}>Cancel link</button>}
          <button className="btn-quiet" disabled={busy} onClick={create}>{status ? "Create a new link" : "Create welcome link"}</button>
        </div>
      </div>
      {error && <div className="mt-2"><ErrorNote message={error} /></div>}
      {issued && (
        <div className="mt-3 rounded border border-rule bg-paper px-4 py-3" role="status">
          <p>Link created for {firstName}. Copy the email from the <Link href="/outbox" className="text-dossier hover:underline">Outbox</Link>, then give {firstName} this code by phone or message:</p>
          <p className="my-2 text-xl font-semibold tracking-[0.3em]" aria-label={`Code ${issued.code.split("").join(" ")}`}>{issued.code}</p>
          <p className="text-sm text-ink-soft">It isn't in the email, and this is the only time it is shown. Creating a new link cancels this one.</p>
          <button className="btn-ghost mt-1" onClick={() => setIssued(null)}>Done</button>
        </div>
      )}
      {cancelling && <CancelDialog empId={empId} onClose={() => setCancelling(false)} />}
    </div>
  );
}

function CancelDialog({ empId, onClose }: { empId: string; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/hr/people/${empId}/welcome-link/cancel`, { body: { reason } });
      mutate(() => true);
      onClose();
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <Dialog open onClose={onClose} title="Cancel the welcome link">
      <form onSubmit={submit} className="space-y-4">
        <p className="text-ink-soft">The link stops working right away. You can create a new one later.</p>
        <label className="block">
          <span className="label">Reason</span>
          <input className="field" required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: sent to the wrong address" />
          <span className="mt-1 block text-sm text-ink-soft">Saved in the change history with your name.</span>
        </label>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Keep it</button>
          <button className="btn-primary" disabled={busy || !reason.trim()}>Cancel link</button>
        </div>
      </form>
    </Dialog>
  );
}
