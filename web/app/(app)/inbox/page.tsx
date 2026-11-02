"use client";
import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Ticket } from "@/lib/types";
import { Avatar } from "@/components/Shell";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { Status } from "@/components/Status";
import { useMe } from "@/components/Me";

export default function Inbox() {
  const me = useMe();
  const [tab, setTab] = useState<"open" | "closed">("open");
  const { data } = useSWR<Ticket[]>(me.role === "hr" ? `/hr/tickets?status=${tab}` : null, fetcher);
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => { setSelected(data?.[0]?.id ?? null); }, [data]);
  if (me.role !== "hr") return <Empty title="The request inbox is for HR" />;
  const t = data?.find((x) => x.id === selected);

  return (
    <>
      <PageHeader title="Request inbox" description="Questions employees sent to HR. Replying closes the request and notifies them." />
      <div role="tablist" className="mb-4 flex gap-1 border-b border-rule">
        {(["open", "closed"] as const).map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`-mb-px border-b-2 px-3 pb-2.5 ${tab === k ? "border-dossier text-ink font-medium" : "border-transparent text-ink-soft hover:text-ink"}`}>
            {k === "open" ? "Open" : "Answered"}
          </button>
        ))}
      </div>
      {data && data.length === 0 ? (
        <div className="panel"><Empty title={tab === "open" ? "No open requests" : "Nothing answered yet"} /></div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <ul className="panel divide-y divide-rule overflow-hidden self-start">
            {data?.map((x) => (
              <li key={x.id}>
                <button onClick={() => setSelected(x.id)} aria-current={x.id === selected ? "true" : undefined}
                        className={`w-full px-4 py-3 text-left ${x.id === selected ? "bg-dossier-tint" : "hover:bg-paper"}`}>
                  <span className="block truncate font-medium">{x.summary}</span>
                  <span className="block text-sm text-ink-soft">{x.employee_name}, {x.ticket}</span>
                </button>
              </li>
            ))}
          </ul>
          {t && <Detail key={t.id} t={t} />}
        </div>
      )}
    </>
  );
}

function Detail({ t }: { t: Ticket }) {
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send() {
    setBusy(true);
    setError("");
    try {
      await api(`/hr/tickets/${t.id}/reply`, { body: { reply: reply.trim() } });
      mutate(() => true);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return (
    <article className="panel">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rule px-5 py-4">
        <div className="flex items-center gap-3">
          <Avatar name={t.employee_name ?? "?"} size={36} />
          <div>
            <p className="font-medium">{t.employee_name}</p>
            <p className="text-sm text-ink-soft">{t.ticket}, sent {timestamp(t.created_at)}</p>
          </div>
        </div>
        <Status value={t.status} />
      </header>
      <div className="px-5 py-4 space-y-3">
        <p className="font-medium">{t.summary}</p>
        <p className="max-w-[70ch]">{t.question}</p>
        {t.hr_reply ? (
          <div className="border-l-2 border-dossier pl-3 max-w-[70ch]"><p className="text-sm text-ink-soft">Your reply</p><p>{t.hr_reply}</p></div>
        ) : (
          <div className="pt-2">
            <label className="block"><span className="label">Reply to {t.employee_name}</span>
              <textarea className="field h-32 py-2" value={reply} onChange={(e) => setReply(e.target.value)} /></label>
            {error && <div className="mt-2"><ErrorNote message={error} /></div>}
            <div className="mt-3 flex justify-end">
              <button className="btn-primary" disabled={!reply.trim() || busy} onClick={send}>{busy ? "Sending…" : "Send reply and close"}</button>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}
