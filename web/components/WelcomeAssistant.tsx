"use client";
// The assistant on the welcome page. It only knows the new hire's own checklist (the server gives it one tool),
// so there are no confirmation cards here. Same look as the signed-in assistant.
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { ArrowUp } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { Rich } from "@/components/Assistant";

type Msg = { role: "user" | "assistant" | "note"; text: string };
type History = { id: number; role: string; content: string }[];

const SUGGESTIONS = ["What do I need to do first?", "What will HR take care of?", "When is my first day?"];

export function WelcomeAssistant() {
  const { data: history } = useSWR<History>("/welcome-assistant/history", fetcher, { revalidateOnFocus: false });
  const [msgs, setMsgs] = useState<Msg[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (history && msgs === null) setMsgs(history.slice(-12).map((h) => ({ role: h.role as Msg["role"], text: h.content })));
  }, [history, msgs]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);

  async function send(message: string) {
    if (!message.trim() || busy) return;
    setText("");
    setMsgs((m) => [...(m ?? []), { role: "user", text: message }]);
    setBusy(true);
    try {
      const r = await api<{ reply: string }>("/welcome-assistant", { body: { message } });
      setMsgs((m) => [...(m ?? []), { role: "assistant", text: r.reply }]);
    } catch (e) {
      setMsgs((m) => [...(m ?? []), { role: "note", text: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Welcome assistant" className="panel flex h-[32rem] flex-col overflow-hidden lg:h-[calc(100vh-8rem)]">
      <div className="shrink-0 border-b border-rule px-4 py-3">
        <p className="font-medium leading-tight">Ask the assistant</p>
        <p className="text-xs text-ink-soft">It can answer questions about your checklist. For anything else, HR will help.</p>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4" aria-live="polite">
        {msgs?.length === 0 && (
          <div className="pt-2">
            <p className="font-medium">Not sure what comes next?</p>
            <div className="mt-3 flex flex-col items-start gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}
                        className="rounded border border-rule-strong px-3 py-1.5 text-left text-sm hover:border-dossier hover:text-dossier">{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs?.map((m, i) => (
          <div key={i}>
            {m.role === "user" && <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-dossier-tint px-3 py-2">{m.text}</p>}
            {m.role === "assistant" && <div className="max-w-[95%] space-y-2 leading-relaxed"><Rich text={m.text} /></div>}
            {m.role === "note" && <p className="text-center text-xs text-ink-soft">{m.text}</p>}
          </div>
        ))}
        {busy && <p className="text-sm text-ink-soft">Checking…</p>}
        <div ref={end} />
      </div>
      <form className="shrink-0 border-t border-rule p-3" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <div className="flex items-end gap-2 rounded-lg border border-rule-strong bg-surface px-3 py-2 focus-within:border-dossier">
          <textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask a question…"
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
                    className="max-h-32 flex-1 resize-none bg-transparent outline-none placeholder:text-ink-faint" aria-label="Your question" />
          <button className="btn-primary h-8 w-8 px-0" disabled={!text.trim() || busy} aria-label="Send"><ArrowUp size={16} /></button>
        </div>
      </form>
    </section>
  );
}
