"use client";
// A candidate's own page, opened with the private link they got when they applied. It shows where the application stands in plain words (never
// the internal reason for a decision), when their data will be erased, and lets them leave the talent pool or withdraw and erase everything. The AI
// assistant (CandidateAssistant) answers questions about the job and the application, and says it is an AI.
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, ApiError, fetcher } from "@/lib/api";
import { day } from "@/lib/format";
import { Tracking } from "@/lib/types";
import { CandidateAssistant } from "@/components/CandidateAssistant";
import { CareersFrame } from "@/components/CareersFrame";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";

const fullDay = (iso: string) => `${day(iso)} ${iso.slice(0, 4)}`;

export default function Track({ params }: { params: { token: string } }) {
  const path = `/careers/track/${params.token}`;
  const { data, error } = useSWR<Tracking>(path, fetcher, { revalidateOnFocus: true });
  const [erased, setErased] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  async function leavePool() {
    setProblem("");
    try { mutate(path, await api<Tracking>(`${path}/leave-talent-pool`, { body: {} }), { revalidate: false }); }
    catch (e) { setProblem((e as Error).message); }
  }

  async function withdraw() {
    setBusy(true);
    setProblem("");
    try { await api(`${path}/withdraw`, { body: {} }); setConfirming(false); setErased(true); }
    catch (e) { setProblem((e as Error).message); setConfirming(false); }
    finally { setBusy(false); }
  }

  if (erased) {
    return <CareersFrame><div className="panel px-5 py-10 text-center"><p className="font-medium">Your application has been erased</p><p className="mt-1 text-ink-soft">Your details and your CV are deleted. Thank you for your interest.</p></div></CareersFrame>;
  }
  if (error instanceof ApiError && error.status === 404) {
    return <CareersFrame><div className="panel px-5 py-10 text-center"><p className="font-medium">This link isn't valid</p><p className="mt-1 text-ink-soft">The application may have been erased, or the link was replaced by a newer one. Ask HR for a new link.</p></div></CareersFrame>;
  }
  if (error) return <CareersFrame><ErrorNote message={error.message} /></CareersFrame>;
  if (!data) return <CareersFrame><p className="text-ink-soft">Loading…</p></CareersFrame>;

  return (
    <CareersFrame>
      <p className="text-sm text-ink-soft">Your application for</p>
      <h1 className="text-xl font-semibold tracking-[-0.01em]">{data.job_title}</h1>
      <p className="mt-1 text-ink-soft">{data.name}, applied on {fullDay(data.applied_on)}.</p>

      <section aria-labelledby="stage" className="panel mt-5 px-5 py-4">
        <h2 id="stage" className="text-sm font-medium text-ink-soft">Where it stands</h2>
        <p className="mt-1 text-lg font-medium">{data.stage_label}</p>
        <p className="mt-1">{data.message}</p>
      </section>

      <CandidateAssistant token={params.token} />

      <section aria-labelledby="data" className="panel mt-4 px-5 py-4">
        <h2 id="data" className="font-medium">Your data</h2>
        <p className="mt-1 text-sm text-ink-soft">
          {data.erase_on ? <>We will erase your application by <strong className="text-ink">{fullDay(data.erase_on)}</strong>. </> : <>You have been selected, so your details now follow the employee rules. </>}
          {data.talent_pool ? "You agreed to be in our talent pool, so we may contact you about other jobs until then." : "You are not in our talent pool."}
          {data.ai_summary_on && <> On <strong className="text-ink">{fullDay(data.ai_summary_on)}</strong> an AI prepared a summary of your CV against the job's requirements for the hiring team. It quotes your CV and does not score or rank you: a person decides.</>}
        </p>
        {problem && <div className="mt-3"><ErrorNote message={problem} /></div>}
        <div className="mt-4 flex flex-wrap gap-2">
          {data.talent_pool && <button className="btn-quiet" onClick={leavePool}>Leave the talent pool</button>}
          {data.can_withdraw && <button className="btn-danger" onClick={() => setConfirming(true)}>Withdraw and erase my application</button>}
        </div>
      </section>

      {confirming && (
        <Dialog open onClose={() => setConfirming(false)} title="Erase your application?">
          <div className="space-y-4">
            <p>This deletes your details, your CV and your cover letter now. It can't be undone, and you will no longer be considered for this job.</p>
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setConfirming(false)}>Keep it</button>
              <button className="btn-danger" disabled={busy} onClick={withdraw}>Erase everything</button>
            </div>
          </div>
        </Dialog>
      )}
    </CareersFrame>
  );
}
