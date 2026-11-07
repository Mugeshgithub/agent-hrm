"use client";
// The emails the app would send. Nothing is sent from here: HR copies the email, sends it from their own
// mailbox, and marks it as sent. A welcome link is removed from the saved email once it is marked as sent.
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Copy } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { OutboxMessage } from "@/lib/types";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

export default function OutboxPage() {
  const me = useMe();
  const { data, error } = useSWR<OutboxMessage[]>(me.role === "hr" ? "/hr/outbox" : null, fetcher);
  if (me.role !== "hr") return <Empty title="The outbox is for HR">Emails to new hires are prepared here for HR to send.</Empty>;
  return (
    <>
      <PageHeader title="Outbox" description="Emails waiting to be sent. Copy one, send it from your own mailbox, then mark it as sent." />
      {error && <ErrorNote message={error.message} />}
      {data && data.length === 0 && <div className="panel"><Empty title="Nothing to send">Welcome emails appear here when you create a welcome link for a new hire.</Empty></div>}
      <ul className="space-y-4">{data?.map((m) => <Message key={m.id} m={m} />)}</ul>
    </>
  );
}

function Message({ m }: { m: OutboxMessage }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function copy() {
    setError("");
    try {
      await navigator.clipboard.writeText(`To: ${m.to_email}\nSubject: ${m.subject}\n\n${m.body}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { setError("Your browser didn't allow copying. Select the text and copy it by hand."); }
  }
  async function markSent() {
    setBusy(true);
    setError("");
    try { await api(`/hr/outbox/${m.id}/sent`, { body: {} }); await mutate("/hr/outbox"); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return (
    <li className="panel">
      <div className="flex flex-col gap-3 px-5 pt-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-medium">{m.subject}</p>
          <p className="text-sm text-ink-soft">To {m.employee_name} ({m.to_email}), prepared {timestamp(m.created_at)}</p>
          <p className="mt-0.5 inline-flex items-center gap-1.5 text-sm">
            <span className={`h-2 w-2 rounded-full ${m.sent_at ? "bg-ok" : "bg-wait"}`} aria-hidden />
            {m.sent_at ? `Sent by ${m.sent_by_name ?? "HR"}, ${timestamp(m.sent_at)}` : "Waiting to be sent"}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button className="btn-quiet" onClick={copy}><Copy size={14} aria-hidden className="mr-1.5" />{copied ? "Copied" : "Copy email"}</button>
          {!m.sent_at && <button className="btn-primary" disabled={busy} onClick={markSent}>Mark as sent</button>}
        </div>
      </div>
      {error && <div className="px-5 pt-3"><ErrorNote message={error} /></div>}
      <pre className="m-5 whitespace-pre-wrap break-words rounded border border-rule bg-paper px-4 py-3 font-sans text-sm leading-relaxed">{m.body}</pre>
    </li>
  );
}
