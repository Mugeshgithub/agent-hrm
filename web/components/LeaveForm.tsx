"use client";
import { useEffect, useState } from "react";
import { mutate } from "swr";
import { api } from "@/lib/api";
import { leaveLabel, range, toISO } from "@/lib/format";
import { Dialog } from "./Dialog";
import { ErrorNote } from "./PageHeader";

type Preview = { working_days: number; available: number; after: number; enough: boolean; in_past: boolean };

export function LeaveForm({ open, onClose }: { open: boolean; onClose: () => void }) {
  const today = toISO(new Date());
  const [type, setType] = useState("casual");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => { if (!open) { setStart(""); setEnd(""); setPreview(null); setError(""); } }, [open]);

  // live preview: working days and the balance after approval, before anything is saved
  useEffect(() => {
    setError("");
    if (!start || !end) return setPreview(null);
    if (end < start) { setPreview(null); return setError("The end date is before the start date."); }
    api<Preview>("/leave/preview", { body: { leave_type: type, start_date: start, end_date: end } })
      .then(setPreview).catch((e) => setError(e.message));
  }, [type, start, end]);

  async function submit() {
    setSaving(true);
    setError("");
    try {
      await api("/leave", { body: { leave_type: type, start_date: start, end_date: end } });
      await mutate(() => true);           // refresh every page's data
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const blocked = !preview || !preview.enough || preview.in_past || preview.working_days === 0;
  const paid = type === "paid";      // paid leave is counted Monday to Saturday, not as "working days"
  const unit = (n: number) => (paid ? "" : "working ") + (n === 1 ? "day" : "days");

  return (
    <Dialog open={open} onClose={onClose} title="Request time off">
      <div className="space-y-4">
        <fieldset>
          <legend className="label">Type</legend>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {["casual", "sick", "paid"].map((t) => (
              <label key={t} className={`flex cursor-pointer items-center gap-2 rounded border px-3 h-10 ${
                type === t ? "border-dossier bg-dossier-tint" : "border-rule-strong hover:bg-paper"}`}>
                <input type="radio" name="type" value={t} checked={type === t} onChange={() => setType(t)} className="accent-dossier" />
                {leaveLabel(t)}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid grid-cols-2 gap-3">
          <label><span className="label">First day</span>
            <input type="date" className="field" min={today} value={start}
                   onChange={(e) => { setStart(e.target.value); if (!end || end < e.target.value) setEnd(e.target.value); }} />
          </label>
          <label><span className="label">Last day</span>
            <input type="date" className="field" min={start || today} value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
        </div>

        {preview && (
          <div className="rounded border border-rule bg-paper px-4 py-3">
            <p className="font-medium">{range(start, end)}: {preview.working_days} {unit(preview.working_days)}</p>
            <p className="text-sm text-ink-soft mt-0.5">
              {preview.in_past ? "Leave can't start in the past." :
               preview.working_days === 0 ? `There are no ${paid ? "days to take" : "working days"} in these dates.` :
               preview.enough ? `You'll have ${preview.after} ${leaveLabel(type).toLowerCase()} days left once it's approved.` :
               `You have ${preview.available} ${leaveLabel(type).toLowerCase()} days available, including requests still waiting.`}
            </p>
            {paid && !preview.in_past && preview.working_days > 0 &&
              <p className="text-sm text-ink-soft mt-0.5">Paid leave counts Monday to Saturday. Sundays and public holidays aren't counted.</p>}
          </div>
        )}
        {error && <ErrorNote message={error} />}

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={blocked || saving} onClick={submit}>
            {saving ? "Sending…" : "Send to my manager"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
