"use client";
// HR: how long candidate data is kept. The rules are in retention.py and cite the CNIL; the page only shows them and lets HR choose how many months
// a talent pool is kept, up to the CNIL's two years. The same erasure runs every day from recruitment_jobs.py; here HR can run it now.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { RetentionSettings } from "@/lib/types";
import { ErrorNote } from "@/components/PageHeader";

export function RetentionCard() {
  const { data } = useSWR<RetentionSettings>("/hr/recruitment/settings", fetcher);
  const [months, setMonths] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const shown = months === "" ? String(data.retention_months) : months;

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNote("");
    try { await api("/hr/recruitment/settings", { body: { retention_months: shown } }); setMonths(""); setNote("Saved."); mutate("/hr/recruitment/settings"); }
    catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  async function run() {
    setBusy(true);
    setError("");
    setNote("");
    try {
      const done = await api<{ erased: number }>("/hr/recruitment/retention-run", { body: {} });
      setNote(done.erased === 0 ? "Nobody was due today." : `Erased ${done.erased} ${done.erased === 1 ? "candidate" : "candidates"}.`);
      mutate(() => true);
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <section aria-labelledby="retention" className="panel mt-8 px-5 py-4">
      <h2 id="retention" className="font-medium">Candidate data</h2>
      <p className="mt-1 text-sm text-ink-soft">
        A candidate who agrees to the talent pool is kept for at most the months below after our last contact with them. Everyone else is erased {data.not_selected_days} days
        after "Not selected", or {data.closed_job_days} days after the job closes if they never got a decision. Someone who is hired is not erased here. A daily job does this
        (<code>recruitment_jobs.py</code>); every erasure is logged without a name, and only the stages, dates and reasons stay.
      </p>
      <form onSubmit={save} className="mt-3 flex flex-wrap items-end gap-3">
        <label className="block"><span className="label">Talent pool: months after the last contact</span>
          <input className="field w-28" inputMode="numeric" value={shown} onChange={(e) => setMonths(e.target.value)} aria-describedby="retention-max" /></label>
        <button className="btn-primary" disabled={busy || months === ""}>Save</button>
        <button type="button" className="btn-quiet" disabled={busy} onClick={run}>Run the erasure now</button>
      </form>
      <p id="retention-max" className="mt-2 text-sm text-ink-soft">
        From 1 to {data.max_months}. The CNIL recommends keeping a talent pool two years at the most from the last contact, and only for people who agreed.
        Source: <a className="text-dossier hover:underline" href={data.url} target="_blank" rel="noreferrer">{data.cite}</a>. Erased so far: {data.erased_so_far}.
      </p>
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}
      {note && <p role="status" className="mt-3 text-sm text-ink-soft">{note}</p>}
    </section>
  );
}
