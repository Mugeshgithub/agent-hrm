"use client";
// The interview on a candidate's tracking page (interviews.py). A person offered some times: the candidate chooses one with a button, or says that none
// suit them; a confirmed time can be added to a calendar or given up. Nothing here is decided by an AI, and the page shows nothing until a person offers times.
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { InterviewView } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";

export function CandidateInterview({ token }: { token: string }) {
  const path = `/careers/track/${token}/interview`;
  const { data } = useSWR<InterviewView>(path, fetcher);
  const [choosing, setChoosing] = useState<{ value: string; label: string } | null>(null);
  const [declining, setDeclining] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!data || data.status === "none") return null;

  async function run(action: "choose" | "cancel", slot?: string) {
    setBusy(true);
    setError("");
    try {
      await api(`${path}/${action}`, { body: action === "choose" ? { slot } : {} });
      await mutate(path);
      setChoosing(null);
      setDeclining(false);
    } catch (e) {
      setError((e as Error).message);
      setChoosing(null);
      setDeclining(false);
      mutate(path);
    } finally { setBusy(false); }
  }

  const details = [data.duration_min ? `${data.duration_min} minutes` : "", data.place ?? ""].filter(Boolean).join(" · ");
  return (
    <section aria-labelledby="interview" className="panel mt-4 px-5 py-4">
      {data.status === "offered" && (
        <>
          <h2 id="interview" className="font-medium">Choose your interview time</h2>
          <p className="mt-1 text-sm text-ink-soft">We would like to meet you. Pick the time that suits you: it is booked when you confirm it. All times are Paris time.</p>
          {details && <p className="mt-2 text-sm">{details}</p>}
          {data.note && <p className="mt-1 text-sm text-ink-soft">{data.note}</p>}
          <div className="mt-3 flex flex-col items-start gap-2">
            {data.slots?.map((s) => (
              <button key={s.value} className="btn-quiet" disabled={s.past} onClick={() => { setError(""); setChoosing(s); }}>{s.label}{s.past ? " (gone)" : ""}</button>
            ))}
          </div>
          <button className="btn-quiet mt-3" onClick={() => { setError(""); setDeclining(true); }}>None of these suit me</button>
        </>
      )}
      {data.status === "confirmed" && data.chosen && (
        <>
          <h2 id="interview" className="font-medium">Your interview is confirmed</h2>
          <p className="mt-2 text-lg font-medium">{data.chosen.label}</p>
          <p className="mt-1 text-sm text-ink-soft">Paris time{details ? ` · ${details}` : ""}</p>
          {data.note && <p className="mt-1 text-sm text-ink-soft">{data.note}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            <a className="btn-quiet" href={`/api${path}.ics`}>Add to my calendar</a>
            <button className="btn-quiet" onClick={() => { setError(""); setDeclining(true); }}>I can't make it</button>
          </div>
        </>
      )}
      {data.status === "cancelled" && (
        <>
          <h2 id="interview" className="font-medium">Your interview was cancelled</h2>
          <p className="mt-1 text-sm text-ink-soft">
            {data.cancelled_by === "candidate" ? "You asked for other times. HR has been told and a person will offer you new ones." : "A person will offer you other times."}
          </p>
        </>
      )}
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}

      {choosing && (
        <Dialog open onClose={() => setChoosing(null)} title="Confirm your interview time">
          <div className="space-y-4">
            <p>{choosing.label}, Paris time.</p>
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setChoosing(null)}>Back</button>
              <button className="btn-primary" disabled={busy} onClick={() => run("choose", choosing.value)}>Confirm this time</button>
            </div>
          </div>
        </Dialog>
      )}
      {declining && (
        <Dialog open onClose={() => setDeclining(false)} title={data.status === "confirmed" ? "Give up this interview time?" : "None of these times suit you?"}>
          <div className="space-y-4">
            <p>HR and the hiring manager will be told, and a person will offer you other times. We cannot say when.</p>
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setDeclining(false)}>Keep it</button>
              <button className="btn-primary" disabled={busy} onClick={() => run("cancel")}>Ask for other times</button>
            </div>
          </div>
        </Dialog>
      )}
    </section>
  );
}
