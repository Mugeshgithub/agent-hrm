"use client";
import { Fragment, ReactNode, useEffect, useRef, useState } from "react";
import useSWR, { mutate } from "swr";
import { ArrowUp, X } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { leaveLabel, range } from "@/lib/format";
import { useMe } from "./Me";
import { MicButton, useSpeechSupported } from "./MicButton";

type Item = { input: Record<string, any>; details: Record<string, any> };
// one card, however many actions it lists: `details` and `input` are the first item's, `items` is every one, in order
type Card = { tool: string; details: Record<string, any>; input: Record<string, any>; items?: Item[] };
type Msg = { role: "user" | "assistant" | "note"; text: string; card?: Card; resolved?: boolean };
type History = { id: number; role: string; content: string }[];

const SUGGESTIONS: Record<string, string[]> = {
  employee: ["How many days of leave do I have left?", "What does my contract say about notice?",
             "What does French law say about paid leave?"],
  manager: ["Show me my team's pending requests", "Who on my team is off next week?",
            "What does French law say about the maximum working day?"],
  hr: ["Show me the open HR tickets", "What does the policy say about carry-over?",
       "Can someone be fired during sick leave after a workplace accident?"],
};

export function Assistant({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useMe();
  const { data: history } = useSWR<History>(open ? "/assistant/history" : null, fetcher, { revalidateOnFocus: false });
  const [msgs, setMsgs] = useState<Msg[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [voiceProblem, setVoiceProblem] = useState<string | null>(null);
  const speech = useSpeechSupported();            // no speech recognition in this browser (Firefox): no mic button
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => setMsgs(null), [me.id]);           // new person, new conversation
  useEffect(() => {
    if (history && msgs === null) {
      setMsgs(history.slice(-12).map((h) => h.content.startsWith("[In the app")
        ? { role: "note", text: h.content.includes("confirmed") ? "You confirmed the action." : "You cancelled the action." }
        : { role: h.role as Msg["role"], text: h.content }));
    }
  }, [history, msgs]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);

  async function send(message: string) {
    if (!message.trim() || busy) return;
    setText("");
    setMsgs((m) => [...(m ?? []), { role: "user", text: message }]);
    setBusy(true);
    try {
      const r = await api<{ reply: string; confirm: Card | null }>("/assistant", { body: { message } });
      setMsgs((m) => [...(m ?? []), { role: "assistant", text: r.reply, card: r.confirm ?? undefined }]);
      mutate((k) => typeof k === "string" && !k.startsWith("/assistant"));
    } catch (e) {
      setMsgs((m) => [...(m ?? []), { role: "note", text: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  async function decide(index: number, accept: boolean) {
    setBusy(true);
    try {
      const r = await api<{ reply: string }>("/assistant/confirm", { body: { accept } });
      setMsgs((m) => (m ?? []).map((x, i) => (i === index ? { ...x, resolved: true } : x))
        .concat({ role: "note", text: accept ? r.reply : "Cancelled. Nothing was changed." }));
      await mutate((k) => typeof k === "string" && !k.startsWith("/assistant"));
    } catch (e) {
      setMsgs((m) => [...(m ?? []), { role: "note", text: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;
  return (
    <aside aria-label="HR assistant"
           className="app-chrome fixed inset-0 z-40 flex flex-col bg-surface xl:inset-y-0 xl:left-auto xl:right-0 xl:w-[420px] xl:border-l xl:border-rule">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-rule px-4">
        <div>
          <p className="font-medium leading-tight">HR assistant</p>
          <p className="text-xs text-ink-soft">Uses your data, the handbook and the Code du travail</p>
        </div>
        <button className="btn-ghost h-8 w-8 px-0" onClick={onClose} aria-label="Close assistant"><X size={16} /></button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4" aria-live="polite">
        {msgs?.length === 0 && (
          <div className="pt-6">
            <p className="font-medium">Ask about your leave, pay or HR rules.</p>
            <p className="mt-1 text-sm text-ink-soft">Before anything changes, you'll see a card to confirm. Legal answers cite the article they come from.</p>
            <div className="mt-4 flex flex-col items-start gap-2">
              {(SUGGESTIONS[me.role] ?? SUGGESTIONS.employee).map((s) => (
                <button key={s} onClick={() => send(s)} className="rounded border border-rule-strong px-3 py-1.5 text-left text-sm hover:border-dossier hover:text-dossier">{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs?.map((m, i) => (
          <Fragment key={i}>
            {m.role === "user" && <p className="ml-auto max-w-[85%] w-fit rounded-lg bg-dossier-tint px-3 py-2">{m.text}</p>}
            {m.role === "assistant" && <div className="max-w-[95%] space-y-2 leading-relaxed"><Rich text={m.text} /></div>}
            {m.role === "note" && (
              <p className={`text-xs text-ink-soft ${m.text.includes("\n") ? "whitespace-pre-line text-left" : "text-center"}`}>{m.text}</p>
            )}
            {m.card && !m.resolved && <ConfirmCard card={m.card} busy={busy} onDecide={(a) => decide(i, a)} />}
          </Fragment>
        ))}
        {busy && <p className="text-sm text-ink-soft">Checking…</p>}
        <div ref={end} />
      </div>

      <form className="border-t border-rule p-3" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <div className="flex items-end gap-2 rounded-lg border border-rule-strong bg-surface px-3 py-2 focus-within:border-dossier">
          <textarea ref={input} rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask a question…"
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
                    className="max-h-32 flex-1 resize-none bg-transparent outline-none placeholder:text-ink-faint" aria-label="Your question" />
          {speech && <MicButton text={text} disabled={busy} onProblem={setVoiceProblem} box={input}
                                onText={setText} />}
          <button className="btn-primary h-8 w-8 px-0" disabled={!text.trim() || busy} aria-label="Send"><ArrowUp size={16} /></button>
        </div>
        {speech && (
          <p className="mt-1.5 text-xs text-ink-soft" role={voiceProblem ? "alert" : undefined}>
            {voiceProblem ?? "Your browser's speech service transcribes it. We only get what you send."}
          </p>
        )}
      </form>
    </aside>
  );
}

// what the assistant wants to do, in plain words; nothing happens until "Confirm"
function ConfirmCard({ card, busy, onDecide }: { card: Card; busy: boolean; onDecide: (accept: boolean) => void }) {
  const d = card.details, i = card.input;
  const items = card.items ?? [{ input: i, details: d }];
  const listed = card.tool === "cancel_leave" || card.tool === "decide_leave";      // one card can list several of these, each with its date and weekday
  const many = items.length > 1;
  const rows: [string, ReactNode][] =
    card.tool === "apply_leave" ? [
      ["Type", leaveLabel(d.leave_type)], ["Dates", range(d.start_date, d.end_date)],
      [d.leave_type === "paid" ? "Days (Mon–Sat)" : "Working days", d.working_days], ["Left after approval", `${d.balance_after_approval} days`]]
    : card.tool === "create_hr_ticket" ? [["Sent to HR", d.send_to_HR]]
    : card.tool === "reply_to_ticket" ? [["Ticket", `HR-${String(i.ticket_id).padStart(4, "0")}`], ["Reply", i.reply]]
    : Object.entries(d).map(([k, v]) => [k, String(v)]);
  const title: Record<string, string> = {
    apply_leave: "Send this leave request?",
    cancel_leave: many ? `Withdraw these ${items.length} requests?` : "Withdraw this request?",
    decide_leave: many ? `Save these ${items.length} decisions?` : "Save this decision?",
    create_hr_ticket: "Send this to HR?", reply_to_ticket: "Send this reply and close the ticket?",
  };
  return (
    <div className="rounded-lg border border-dossier/40 bg-dossier-tint/60 p-3">
      <p className="font-medium">{title[card.tool] ?? "Confirm this action?"}</p>
      {listed ? (
        <ul className="mt-2 space-y-1.5 text-sm">
          {items.map((it, n) => <li key={n} className="break-words">{itemLine(card.tool, it)}</li>)}
        </ul>
      ) : (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          {rows.map(([k, v]) => (<Fragment key={k}><dt className="text-ink-soft">{k}</dt><dd>{v}</dd></Fragment>))}
        </dl>
      )}
      <div className="mt-3 flex gap-2">
        <button className="btn-primary h-8" disabled={busy} onClick={() => onDecide(true)}>Confirm</button>
        <button className="btn-quiet h-8" disabled={busy} onClick={() => onDecide(false)}>Cancel</button>
      </div>
    </div>
  );
}

// one item of a card: its date with the weekday (built by the server: "Fri 20 Nov 2026"), the kind of leave and the days
function itemLine(tool: string, { input, details: d }: Item): ReactNode {
  if (!d.when) return `Request #${input.request_id}`;
  const what = (<><span className="font-medium">{d.when}</span><span className="text-ink-soft"> · {leaveLabel(d.leave_type)} · {d.days} {d.days === 1 ? "day" : "days"}</span></>);
  if (tool === "cancel_leave") return what;
  return (<>{d.employee_name}: {what}<br /><span className="text-ink-soft">{input.decision === "approve" ? "Approve" : `Reject: ${input.reason}`}</span></>);
}

// light formatting: paragraphs, bullet lists, **bold**, and law articles shown as references
export function Rich({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        if (lines.every((l) => /^\s*[-*•]\s/.test(l))) {
          return <ul key={i} className="list-disc pl-5 space-y-1">{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*•]\s/, ""))}</li>)}</ul>;
        }
        return <p key={i}>{lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l)}</Fragment>)}</p>;
      })}
    </>
  );
}

function inline(s: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|\b[LRD]\.?\s?\d{4}(?:-\d+)+\b)/g).map((part, i) => {
    if (/^\*\*.+\*\*$/.test(part)) return <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>;
    if (/^[LRD]\.?\s?\d{4}/.test(part)) {
      const num = part.replace(/[.\s]/g, "");
      return (
        <span key={i} title={`Code du travail, article ${num}`}
              className="whitespace-nowrap rounded-sm bg-dossier-tint px-1 font-medium text-dossier">{num}</span>
      );
    }
    return part;
  });
}
