"use client";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { dayWithWeekday, relative } from "@/lib/format";
import { Holiday, MyHolidays } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote, PageHeader } from "@/components/PageHeader";
import { Status } from "@/components/Status";

export default function Holidays() {
  const [year, setYear] = useState(new Date().getFullYear());
  const { data } = useSWR<MyHolidays>(`/holidays?year=${year}`, fetcher);
  const [choosing, setChoosing] = useState<Holiday | null>(null);
  const [withdrawing, setWithdrawing] = useState<Holiday | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = () => mutate((k) => typeof k === "string" && (k.startsWith("/holidays") || k === "/leave" || k === "/home" || k.startsWith("/calendar")));

  async function take() {
    if (!choosing) return;
    setBusy(true); setError("");
    try { await api("/holidays/floater", { body: { day: choosing.day } }); setChoosing(null); refresh(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function withdraw() {
    if (!withdrawing?.my_request) return;
    setBusy(true); setError("");
    try { await api(`/leave/${withdrawing.my_request.id}/cancel`, { body: {} }); setWithdrawing(null); refresh(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const mandatory = (data?.holidays ?? []).filter((h) => h.kind === "mandatory");
  const floaters = (data?.holidays ?? []).filter((h) => h.kind === "floater");
  const thisYear = new Date().getFullYear();

  return (
    <>
      <PageHeader title="Holidays" description={data ? `Your holiday plan is ${data.plan.name}. ${data.plan.description}` : "Your public holidays and floating holidays."} />

      <div role="tablist" aria-label="Year" className="mb-5 flex gap-1">
        {[thisYear, thisYear + 1].map((y) => (
          <button key={y} role="tab" aria-selected={y === year} onClick={() => setYear(y)}
                  className={`rounded px-3 py-1.5 ${y === year ? "bg-dossier-tint text-dossier font-medium" : "text-ink-soft hover:bg-surface"}`}>{y}</button>
        ))}
      </div>

      {!data ? <p className="text-ink-soft">Loading…</p> : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section aria-labelledby="mand" className="panel">
            <div className="px-5 pt-5 pb-3">
              <h2 id="mand" className="font-medium">Public holidays</h2>
              <p className="text-sm text-ink-soft">Everyone on your plan is off. These days are never counted as leave.</p>
            </div>
            {mandatory.length === 0 ? <p className="px-5 pb-5 text-ink-soft">None set for {year} yet.</p> : (
              <ul className="divide-y divide-rule border-t border-rule">
                {mandatory.map((h) => (
                  <li key={h.day} className={`flex items-baseline justify-between gap-3 px-5 py-2.5 ${h.past ? "text-ink-faint" : ""}`}>
                    <span>{h.name}</span>
                    <span className="text-sm whitespace-nowrap">{dayWithWeekday(h.day)}{h.weekend && <span className="text-ink-soft">, weekend</span>}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="float" className="panel">
            <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
              <div>
                <h2 id="float" className="font-medium">Floating holidays</h2>
                <p className="text-sm text-ink-soft">Optional days. Choose up to {data.floaters.quota} in {year}; your manager approves them.</p>
              </div>
              <div className="text-right shrink-0">
                <p className="text-xl font-semibold leading-none">{data.floaters.left}</p>
                <p className="text-sm text-ink-soft">of {data.floaters.quota} left</p>
              </div>
            </div>
            {floaters.length === 0 ? <p className="px-5 pb-5 text-ink-soft">Your plan has no floating holidays in {year}.</p> : (
              <ul className="divide-y divide-rule border-t border-rule">
                {floaters.map((h) => {
                  const req = h.my_request;
                  const active = req && req.status !== "rejected";
                  return (
                    <li key={h.day} className="flex items-center justify-between gap-3 px-5 py-3">
                      <span className={h.past ? "text-ink-faint" : ""}>
                        <span className="block">{h.name}</span>
                        <span className="block text-sm text-ink-soft">{dayWithWeekday(h.day)}{!h.past && `, ${relative(h.day)}`}</span>
                      </span>
                      {active ? (
                        <span className="flex flex-col items-end gap-1">
                          <Status value={req!.status} />
                          {!h.past && <button className="text-sm text-no hover:underline" onClick={() => { setError(""); setWithdrawing(h); }}>Withdraw</button>}
                        </span>
                      ) : h.past || h.weekend ? (
                        <span className="text-sm text-ink-faint">{h.weekend && !h.past ? "On a weekend" : "Passed"}</span>
                      ) : (
                        <button className="btn-quiet" disabled={data.floaters.left === 0} onClick={() => { setError(""); setChoosing(h); }}
                                title={data.floaters.left === 0 ? "You've used all your floating holidays this year" : undefined}>
                          {req?.status === "rejected" ? "Ask again" : "Take this day"}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      )}

      <Dialog open={!!choosing} onClose={() => setChoosing(null)} title="Take this floating holiday?">
        {choosing && (
          <div className="space-y-4">
            <p><span className="font-medium">{choosing.name}</span>, {dayWithWeekday(choosing.day)}. It goes to your manager for approval and uses 1 of your {data?.floaters.left} remaining floating holidays. Your leave balance doesn't change.</p>
            {error && <ErrorNote message={error} />}
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setChoosing(null)}>Cancel</button>
              <button className="btn-primary" disabled={busy} onClick={take}>{busy ? "Sending…" : "Send request"}</button>
            </div>
          </div>
        )}
      </Dialog>

      <Dialog open={!!withdrawing} onClose={() => setWithdrawing(null)} title="Withdraw this floating holiday?">
        {withdrawing && (
          <div className="space-y-4">
            <p>{withdrawing.name}, {dayWithWeekday(withdrawing.day)}. You can choose another floating holiday afterwards.</p>
            {error && <ErrorNote message={error} />}
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setWithdrawing(null)}>Keep it</button>
              <button className="btn-danger" disabled={busy} onClick={withdraw}>Withdraw</button>
            </div>
          </div>
        )}
      </Dialog>
    </>
  );
}
