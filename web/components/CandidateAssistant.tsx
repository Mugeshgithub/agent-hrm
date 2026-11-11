"use client";
// The AI assistant on a candidate's tracking page (candidate_assistant.py). The private link is the only key: there is no session, and the server gives
// the assistant three tools (the job post, the privacy notice, a note to HR). It says it is an AI here and in every fixed answer. It never reads the CV,
// never talks about other candidates or chances, and cannot change the application: a person decides.
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { ArrowUp } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { Rich } from "@/components/Assistant";
import { CandidateChat } from "@/lib/types";

type Msg = { role: "user" | "assistant" | "note"; text: string };

const SUGGESTIONS = ["What does this job involve?", "What are the requirements?", "How long do you keep my data?", "Where does my application stand?"];

export function CandidateAssistant({ token }: { token: string }) {
  const path = `/careers/track/${token}/assistant`;
  const { data } = useSWR<CandidateChat>(path, fetcher, { revalidateOnFocus: false });
  const [msgs, setMsgs] = useState<Msg[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (data && msgs === null) setMsgs(data.messages.slice(-20).map((m) => ({ role: m.role, text: m.content })));
  }, [data, msgs]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);

  async function send(message: string) {
    if (!message.trim() || busy) return;
    setText("");
    setMsgs((m) => [...(m ?? []), { role: "user", text: message }]);
    setBusy(true);
    try {
      const r = await api<{ reply: string; noted: boolean }>(path, { body: { message } });
      setMsgs((m) => [...(m ?? []), { role: "assistant", text: r.reply }, ...(r.noted ? [{ role: "note" as const, text: "Your note was passed to HR." }] : [])]);
    } catch (e) {
      setMsgs((m) => [...(m ?? []), { role: "note", text: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  if (!data) return null;
  if (!data.assistant.available) {
    return (
      <section aria-labelledby="assistant" className="panel mt-4 px-5 py-4">
        <h2 id="assistant" className="font-medium">Questions about the job?</h2>
        <p className="mt-1 text-sm text-ink-soft">{data.assistant.reason}</p>
      </section>
    );
  }

  return (
    <section aria-labelledby="assistant" className="panel mt-4 flex h-[30rem] flex-col overflow-hidden">
      <div className="shrink-0 border-b border-rule px-4 py-3">
        <h2 id="assistant" className="font-medium leading-tight">Ask the AI assistant</h2>
        <p className="mt-1 text-xs text-ink-soft">{data.assistant.intro}</p>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4" aria-live="polite">
        {msgs?.length === 0 && (
          <div className="pt-1">
            <p className="text-sm text-ink-soft">For example:</p>
            <div className="mt-2 flex flex-col items-start gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}
                        className="rounded border border-rule-strong px-3 py-1.5 text-left text-sm hover:border-dossier hover:text-dossier">{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs?.map((m, i) => (
          <div key={i}>
            {m.role === "user" && <p className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-lg bg-dossier-tint px-3 py-2">{m.text}</p>}
            {m.role === "assistant" && <div className="max-w-[95%] space-y-2 leading-relaxed"><Rich text={m.text} /></div>}
            {m.role === "note" && <p className="text-center text-xs text-ink-soft">{m.text}</p>}
          </div>
        ))}
        {busy && <p className="text-sm text-ink-soft">Thinking…</p>}
        <div ref={end} />
      </div>
      <form className="shrink-0 border-t border-rule p-3" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <div className="flex items-end gap-2 rounded-lg border border-rule-strong bg-surface px-3 py-2 focus-within:border-dossier">
          <textarea rows={1} value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} placeholder="Ask a question…"
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
                    className="max-h-32 flex-1 resize-none bg-transparent outline-none placeholder:text-ink-faint" aria-label="Your question" />
          <button className="btn-primary h-8 w-8 px-0" disabled={!text.trim() || busy} aria-label="Send"><ArrowUp size={16} /></button>
        </div>
      </form>
    </section>
  );
}
