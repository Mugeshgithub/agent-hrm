"use client";
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { dayWithWeekday, timestamp } from "@/lib/format";
import { Holiday } from "@/lib/types";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

type Preview = {
  year: number; people: number; runs_on: string; already_done: { ran_by: string; ran_at: string } | null;
  casual_days_carried: number; casual_days_lapsing: number; sick_days_lapsing: number; people_losing_casual_days: number;
};

export default function LeaveAdmin() {
  const me = useMe();
  const isHR = me.role === "hr";
  const { data: holidays } = useSWR<Holiday[]>(isHR ? "/holidays" : null, fetcher);
  const nextYear = new Date().getFullYear() + 1;
  const { data: preview } = useSWR<Preview>(isHR ? `/hr/leave/year-start?year=${nextYear}` : null, fetcher);
  const years = Array.from(new Set((holidays ?? []).map((h) => h.day.slice(0, 4)))).sort();
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [day, setDay] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  if (!isHR) return <Empty title="Leave settings are for HR" />;

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(""); setNote("");
    try {
      const r = await api<{ note: string; day: string }>("/hr/holidays", { body: { day, name } });
      setNote(r.note); setDay(""); setName(""); setYear(r.day.slice(0, 4));
      mutate("/holidays"); mutate((k) => typeof k === "string" && (k.startsWith("/calendar") || k === "/home" || k === "/leave"));
    } catch (err) { setError((err as Error).message); }
  }

  async function remove(h: Holiday) {
    setError(""); setNote("");
    try {
      await api(`/hr/holidays/${h.day}`, { method: "DELETE" });
      mutate("/holidays");
    } catch (err) { setError((err as Error).message); }
  }

  const shown = (holidays ?? []).filter((h) => h.day.startsWith(year));

  return (
    <>
      <PageHeader title="Leave settings" description="Public holidays are skipped when leave days are counted. Balances are updated automatically at the start of each year." />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <section aria-labelledby="hols" className="panel">
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5 pb-3">
            <h2 id="hols" className="font-medium">Public holidays</h2>
            <div role="tablist" aria-label="Year" className="flex gap-1">
              {years.map((y) => (
                <button key={y} role="tab" aria-selected={y === year} onClick={() => setYear(y)}
                        className={`rounded px-3 py-1 text-sm ${y === year ? "bg-dossier-tint text-dossier font-medium" : "text-ink-soft hover:bg-paper"}`}>{y}</button>
              ))}
            </div>
          </div>
          {shown.length === 0 ? <p className="px-5 pb-5 text-ink-soft">No public holidays set for {year}.</p> : (
            <ul className="divide-y divide-rule border-t border-rule">
              {shown.map((h) => (
                <li key={h.day} className="flex items-center justify-between gap-3 px-5 py-2.5">
                  <span>
                    <span className="block">{h.name}</span>
                    <span className="block text-sm text-ink-soft">{dayWithWeekday(h.day)}{h.weekend && ", on a weekend"}</span>
                  </span>
                  <button className="text-sm text-no hover:underline" onClick={() => remove(h)} aria-label={`Remove ${h.name}`}>Remove</button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={add} className="flex flex-col gap-3 border-t border-rule px-5 py-4 sm:flex-row sm:items-end">
            <label className="block sm:w-44"><span className="label">Date</span>
              <input type="date" className="field" required value={day} onChange={(e) => setDay(e.target.value)} /></label>
            <label className="block flex-1"><span className="label">Name</span>
              <input className="field" required value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: company day off" /></label>
            <button className="btn-primary">Add holiday</button>
          </form>
          {(note || error) && <div className="px-5 pb-4">{error ? <ErrorNote message={error} /> : <p className="text-sm text-ink-soft">{note}</p>}</div>}
        </section>

        <section aria-labelledby="yearly" className="panel px-5 py-5">
          <h2 id="yearly" className="font-medium">Start of {preview?.year ?? nextYear}</h2>
          {!preview ? <p className="mt-2 text-ink-soft">Loading…</p> : preview.already_done ? (
            <p className="mt-2">Done on {timestamp(preview.already_done.ran_at)} for {preview.people} people.</p>
          ) : (
            <>
              <p className="mt-1 text-sm text-ink-soft">Runs automatically on 1 January for everyone still employed. Here's what it will do with today's balances.</p>
              <dl className="mt-4 space-y-3">
                <Row label="People updated" value={preview.people} />
                <Row label="Casual days carried over" value={preview.casual_days_carried} hint="Up to 5 per person" />
                <Row label="Casual days that lapse" value={preview.casual_days_lapsing} hint={`${preview.people_losing_casual_days} people have more than 5`} />
                <Row label="Sick days that lapse" value={preview.sick_days_lapsing} hint="Sick leave doesn't carry over" />
              </dl>
              <p className="mt-4 rounded border border-rule bg-paper px-3 py-2 text-sm">Then everyone gets 12 casual and 6 sick days for {preview.year}. Each change appears in the person's balance history.</p>
            </>
          )}
        </section>
      </div>
    </>
  );
}

function Row({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt>{label}{hint && <span className="block text-sm text-ink-soft">{hint}</span>}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
