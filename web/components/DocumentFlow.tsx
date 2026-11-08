"use client";
// What happens when a new hire sends a document, from the file chosen to "yes, that's right". The server does the
// work (documents.py): it checks the file, has Claude read it, and checks what was read. This only picks the
// file, shows what the app read and asks the person to confirm, or says what to fix and asks for another file.
import { useRef, useState } from "react";
import { mutate } from "swr";
import { Paperclip } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { UploadResult, WelcomeStep } from "@/lib/types";
import { ErrorNote } from "@/components/PageHeader";

type Flow =
  | { phase: "reading"; step: WelcomeStep; origin: Origin }
  | { phase: "review"; step: WelcomeStep; origin: Origin; docId: number; read: { label: string; value: string }[] }
  | { phase: "refused"; step: WelcomeStep; origin: Origin; problems: string[] }
  | { phase: "error"; step: WelcomeStep; origin: Origin; message: string };
export type Origin = "list" | "chat";

const MAX = 10 * 1024 * 1024;
const LABEL: Record<string, string> = { id: "identity document", iban: "bank details", social_security: "social security number" };

// the steps that take a file, and are still the person's to do
export const sendable = (steps: WelcomeStep[]) => steps.filter((s) => s.document && s.status === "todo");

export function useDocumentFlow(onEvent: (text: string) => void) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const target = useRef<{ step: WelcomeStep; origin: Origin } | null>(null);

  function pick(step: WelcomeStep, origin: Origin) {
    target.current = { step, origin };
    if (input.current) input.current.value = "";
    input.current?.click();
  }

  async function onFile(file: File | undefined) {
    const t = target.current;
    if (!file || !t) return;
    if (file.size > MAX) return setFlow({ phase: "refused", ...t, problems: [`That file is too big (${(file.size / 1048576).toFixed(1)} MB). The limit is 10 MB.`] });
    setFlow({ phase: "reading", ...t });
    try {
      const res = await fetch(`/api/welcome-documents?step=${encodeURIComponent(t.step.key)}&name=${encodeURIComponent(file.name)}`,
        { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : "That didn't work. Try again.");
      const r = data as UploadResult;
      setFlow(r.accepted ? { phase: "review", ...t, docId: r.document_id, read: r.read } : { phase: "refused", ...t, problems: r.problems });
      mutate("/welcome-me");
    } catch (e) { setFlow({ phase: "error", ...t, message: (e as Error).message }); }
  }

  async function answer(accept: boolean) {
    if (flow?.phase !== "review") return;
    const { step, docId } = flow;
    try {
      await api(`/welcome-documents/${docId}/${accept ? "confirm" : "decline"}`, { body: {} });
      setFlow(null);
      await mutate("/welcome-me");
      onEvent(accept ? `[In the app, I confirmed my ${LABEL[step.key]}. What is next?]`
                     : `[In the app, I said what was read from my ${LABEL[step.key]} is not right, so I will send another.]`);
    } catch (e) { setFlow({ phase: "error", step, origin: flow.origin, message: (e as Error).message }); }
  }

  const hidden = <input ref={input} type="file" className="sr-only" tabIndex={-1} aria-hidden
                        accept="application/pdf,image/jpeg,image/png" onChange={(e) => onFile(e.target.files?.[0])} />;
  return { flow, pick, answer, dismiss: () => setFlow(null), hidden };
}

export function FlowCard({ f, answer, retry, dismiss }:
  { f: ReturnType<typeof useDocumentFlow>; answer: (accept: boolean) => void; retry: () => void; dismiss: () => void }) {
  const flow = f.flow;
  if (!flow) return null;
  const what = LABEL[flow.step.key];
  return (
    <div className="rounded border border-rule bg-paper px-4 py-3 text-sm" role="status" aria-live="polite">
      {flow.phase === "reading" && <p>Reading your {what}…</p>}
      {flow.phase === "review" && (
        <>
          <p className="font-medium">Here is what I read from your {what}:</p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            {flow.read.map((r) => (<div key={r.label} className="contents"><dt className="text-ink-soft">{r.label}</dt><dd className="break-all font-medium">{r.value}</dd></div>))}
          </dl>
          <p className="mt-2 text-ink-soft">Is this right? HR will look at the document too.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="btn-primary" onClick={() => answer(true)}>Yes, that's right</button>
            <button className="btn-quiet" onClick={() => answer(false)}>No, I'll send another</button>
          </div>
        </>
      )}
      {flow.phase === "refused" && (
        <>
          <p className="font-medium">I couldn't accept your {what}.</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{flow.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          <p className="mt-2 text-ink-soft">The file was not kept. Please send it again.</p>
          <div className="mt-3 flex gap-2"><button className="btn-primary" onClick={retry}>Send another file</button><button className="btn-ghost" onClick={dismiss}>Close</button></div>
        </>
      )}
      {flow.phase === "error" && (
        <>
          <ErrorNote message={flow.message} />
          <div className="mt-3 flex gap-2"><button className="btn-primary" onClick={retry}>Try again</button><button className="btn-ghost" onClick={dismiss}>Close</button></div>
        </>
      )}
    </div>
  );
}

// the paperclip in the chat: choose which document this is, then the file
export function AttachMenu({ steps, onPick }: { steps: WelcomeStep[]; onPick: (s: WelcomeStep) => void }) {
  const [open, setOpen] = useState(false);
  const list = sendable(steps);
  if (list.length === 0) return null;
  return (
    <div className="relative">
      <button type="button" className="btn-ghost h-8 w-8 px-0" aria-label="Send a document" aria-expanded={open} onClick={() => setOpen((o) => !o)}><Paperclip size={16} /></button>
      {open && (
        <ul className="absolute bottom-10 left-0 z-10 w-56 rounded-lg border border-rule bg-surface py-1 shadow-lg" role="menu">
          <li className="px-3 py-1 text-xs text-ink-soft">This is my…</li>
          {list.map((s) => (
            <li key={s.key}><button role="menuitem" className="block w-full px-3 py-2 text-left hover:bg-paper" onClick={() => { setOpen(false); onPick(s); }}>{LABEL[s.key]}</button></li>
          ))}
        </ul>
      )}
    </div>
  );
}
