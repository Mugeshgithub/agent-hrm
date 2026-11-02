"use client";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Ticket } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { Status } from "@/components/Status";

export default function Requests() {
  const { data } = useSWR<Ticket[]>("/tickets", fetcher);
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [details, setDetails] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function send() {
    setSaving(true);
    setError("");
    try {
      await api("/tickets", { body: { subject: subject.trim(), details: details.trim() } });
      setOpen(false);
      setSubject("");
      setDetails("");
      mutate(() => true);
    } catch (e) { setError((e as Error).message); } finally { setSaving(false); }
  }

  return (
    <>
      <PageHeader title="HR requests" description="Questions the handbook doesn't answer, exceptions, or anything that needs a person in HR. You'll get a notification when they reply."
                  actions={<button className="btn-primary" onClick={() => setOpen(true)}>New request</button>} />
      <section className="panel">
        {!data ? <p className="p-5 text-ink-soft">Loading…</p> : data.length === 0 ? (
          <Empty title="No requests yet">Ask the assistant first; if it can't help, it will offer to send your question here.</Empty>
        ) : (
          <ul className="divide-y divide-rule">
            {data.map((t) => (
              <li key={t.id} className="px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
                  <div className="min-w-0">
                    <p className="font-medium">{t.summary}</p>
                    <p className="text-sm text-ink-soft">{t.ticket}, sent {timestamp(t.created_at)}</p>
                  </div>
                  <Status value={t.status} />
                </div>
                <p className="mt-2 max-w-[70ch]">{t.question}</p>
                {t.hr_reply && (
                  <div className="mt-3 border-l-2 border-dossier pl-3 max-w-[70ch]">
                    <p className="text-sm text-ink-soft">HR replied</p>
                    <p>{t.hr_reply}</p>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <Dialog open={open} onClose={() => setOpen(false)} title="New request to HR">
        <div className="space-y-4">
          <label className="block"><span className="label">Subject</span>
            <input className="field" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={120}
                   placeholder="For example: carry over more casual days" /></label>
          <label className="block"><span className="label">Details</span>
            <textarea className="field h-32 py-2" value={details} onChange={(e) => setDetails(e.target.value)}
                      placeholder="What do you need, and by when?" /></label>
          {error && <ErrorNote message={error} />}
          <div className="flex justify-end gap-2">
            <button className="btn-quiet" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn-primary" disabled={!subject.trim() || !details.trim() || saving} onClick={send}>
              {saving ? "Sending…" : "Send to HR"}
            </button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
