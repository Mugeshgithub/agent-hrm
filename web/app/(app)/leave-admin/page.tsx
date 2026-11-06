"use client";
// HR: holiday plans (like Keka's holiday calendars) and the start-of-year balance reset
import { FormEvent, useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { Plus, Upload } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { dayWithWeekday, timestamp } from "@/lib/format";
import { Holiday, HolidayPlan, Person } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

type PlanDetail = { plan: HolidayPlan; holidays: Holiday[]; years: string[] };
type Preview = {
  year: number; people: number; already_done: { ran_by: string; ran_at: string } | null;
  casual_days_carried: number; casual_days_lapsing: number; sick_days_lapsing: number; people_losing_casual_days: number;
  paid_days_carried: number; paid_days_lapsing: number;
};
type ImportRow = { line: number; date: string; name: string; kind: string; ok: boolean; problem: string | null };
type ImportResult = { dry_run: boolean; rows: ImportRow[]; will_add?: number; added?: number; skipped: number };

const refreshCalendars = () =>
  mutate((k) => typeof k === "string" && (k.startsWith("/hr/holiday-plans") || k.startsWith("/holidays") ||
    k.startsWith("/calendar") || k === "/home" || k === "/leave"));

export default function LeaveAdmin() {
  const me = useMe();
  const isHR = me.role === "hr";
  const { data: plans } = useSWR<HolidayPlan[]>(isHR ? "/hr/holiday-plans" : null, fetcher);
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<"plans" | "year">("plans");
  const [creating, setCreating] = useState(false);
  useEffect(() => { if (plans?.length && !plans.some((p) => p.id === selected)) setSelected(plans[0].id); }, [plans, selected]);

  if (!isHR) return <Empty title="Leave settings are for HR" />;

  return (
    <>
      <PageHeader title="Leave settings" description="Holiday plans decide which days are public holidays for each person. Leave is always counted with the person's own plan." />

      <div role="tablist" aria-label="Section" className="mb-5 flex gap-1 border-b border-rule">
        {([["plans", "Holiday plans"], ["year", "Start of year"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`-mb-px border-b-2 px-3 pb-2.5 pt-1 ${tab === k ? "border-dossier text-ink font-medium" : "border-transparent text-ink-soft hover:text-ink"}`}>{label}</button>
        ))}
      </div>

      {tab === "year" ? <YearStart /> : (
        <div className="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside aria-label="Holiday plans" className="space-y-2">
            {(plans ?? []).map((p) => (
              <button key={p.id} onClick={() => setSelected(p.id)} aria-current={p.id === selected}
                      className={`block w-full rounded-lg border px-4 py-3 text-left ${p.id === selected ? "border-dossier bg-surface" : "border-rule bg-surface hover:border-rule-strong"}`}>
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">{p.name}</span>
                  {p.is_default && <span className="text-xs text-ink-soft">Default</span>}
                </span>
                <span className="block text-sm text-ink-soft">{p.people} {p.people === 1 ? "person" : "people"}, {p.floater_quota} floating {p.floater_quota === 1 ? "day" : "days"} a year</span>
              </button>
            ))}
            <button className="btn-quiet w-full" onClick={() => setCreating(true)}><Plus size={16} aria-hidden /> New plan</button>
          </aside>
          {selected && <PlanView key={selected} id={selected} plans={plans ?? []} onDeleted={() => setSelected(null)} />}
        </div>
      )}
      {creating && <NewPlanDialog plans={plans ?? []} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setSelected(id); }} />}
    </>
  );
}

function PlanView({ id, plans, onDeleted }: { id: number; plans: HolidayPlan[]; onDeleted: () => void }) {
  const { data } = useSWR<PlanDetail>(`/hr/holiday-plans/${id}/holidays`, fetcher);
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [removing, setRemoving] = useState<Holiday | null>(null);
  const [dialog, setDialog] = useState<"" | "edit" | "import" | "assign" | "delete">("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [f, setF] = useState({ day: "", name: "", kind: "mandatory" });

  if (!data) return <p className="text-ink-soft">Loading…</p>;
  const plan = plans.find((p) => p.id === id) ?? data.plan;
  const years = Array.from(new Set([...data.years, String(new Date().getFullYear()), String(new Date().getFullYear() + 1)])).sort();
  const shown = data.holidays.filter((h) => h.day.startsWith(year));
  const floaters = shown.filter((h) => h.kind === "floater").length;

  async function act(fn: () => Promise<unknown>, done?: (r: unknown) => void) {
    setError(""); setNote("");
    try { const r = await fn(); done?.(r); refreshCalendars(); }
    catch (e) { setError((e as Error).message); }
  }

  function add(e: FormEvent) {
    e.preventDefault();
    act(() => api<{ note: string; day: string }>(`/hr/holiday-plans/${id}/holidays`, { body: f }),
        (r) => { const x = r as { note: string; day: string }; setNote(x.note); setYear(x.day.slice(0, 4)); setF({ day: "", name: "", kind: f.kind }); });
  }

  return (
    <section aria-label={plan.name} className="panel min-w-0">
      <div className="flex flex-col gap-3 px-5 pt-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-md font-semibold">{plan.name}</h2>
          {plan.description && <p className="text-ink-soft max-w-[60ch]">{plan.description}</p>}
          <p className="mt-1 text-sm text-ink-soft">{plan.people} {plan.people === 1 ? "person" : "people"} on this plan. Each can choose {plan.floater_quota} floating {plan.floater_quota === 1 ? "holiday" : "holidays"} a year.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-quiet" onClick={() => setDialog("assign")}>Move an office</button>
          <button className="btn-quiet" onClick={() => setDialog("import")}><Upload size={15} aria-hidden /> Import</button>
          <button className="btn-quiet" onClick={() => setDialog("edit")}>Edit plan</button>
          {!plan.is_default && <button className="btn-danger" onClick={() => setDialog("delete")}>Delete</button>}
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between gap-3 border-t border-rule px-5 pt-3">
        <div role="tablist" aria-label="Year" className="flex gap-1">
          {years.map((y) => (
            <button key={y} role="tab" aria-selected={y === year} onClick={() => setYear(y)}
                    className={`rounded px-3 py-1 text-sm ${y === year ? "bg-dossier-tint text-dossier font-medium" : "text-ink-soft hover:bg-paper"}`}>{y}</button>
          ))}
        </div>
        <p className="text-sm text-ink-soft">{shown.length - floaters} public, {floaters} floating</p>
      </div>

      {shown.length === 0 ? <p className="px-5 py-6 text-ink-soft">No holidays in {year} yet. Add them below, or import a file.</p> : (
        <div className="overflow-x-auto">
          <table className="mt-2 w-full min-w-[560px]">
            <thead><tr className="border-b border-rule">
              <th className="table-head px-5 py-2">Holiday</th><th className="table-head px-3 py-2">Date</th>
              <th className="table-head px-3 py-2">Type</th><th className="table-head px-5 py-2"><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody className="divide-y divide-rule">
              {shown.map((h) => (
                <tr key={h.day} className={h.past ? "text-ink-faint" : ""}>
                  <td className="px-5 py-2.5">{h.name}</td>
                  <td className="px-3 py-2.5 whitespace-nowrap">{dayWithWeekday(h.day)}{h.weekend && <span className="text-sm">, weekend</span>}</td>
                  <td className="px-3 py-2.5">
                    {h.kind === "floater"
                      ? <span className="text-sm">Floating{h.floaters_taken ? <span className="text-ink-soft">, chosen by {h.floaters_taken}</span> : null}</span>
                      : <span className="text-sm">Public holiday</span>}
                  </td>
                  <td className="px-5 py-2.5 text-right whitespace-nowrap">
                    {h.past ? <span className="text-sm">Passed</span> : (
                      <span className="inline-flex gap-4 text-sm">
                        <button className="text-dossier hover:underline"
                                onClick={() => act(() => api(`/hr/holiday-plans/${id}/holidays/${h.day}`, { method: "PATCH", body: { kind: h.kind === "floater" ? "mandatory" : "floater" } }))}>
                          {h.kind === "floater" ? "Make public" : "Make floating"}
                        </button>
                        <button className="text-no hover:underline" onClick={() => { setError(""); setRemoving(h); }}>Remove</button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form onSubmit={add} className="grid gap-3 border-t border-rule px-5 py-4 sm:grid-cols-[150px_minmax(0,1fr)_150px_auto] sm:items-end">
        <label className="block"><span className="label">Date</span><input type="date" className="field" required value={f.day} onChange={(e) => setF({ ...f, day: e.target.value })} /></label>
        <label className="block"><span className="label">Name</span><input className="field" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="For example: company day off" /></label>
        <label className="block"><span className="label">Type</span>
          <select className="field" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="mandatory">Public holiday</option><option value="floater">Floating</option>
          </select></label>
        <button className="btn-primary">Add</button>
      </form>
      {(note || error) && <div className="px-5 pb-4">{error ? <ErrorNote message={error} /> : <p className="text-sm text-ink-soft">{note}</p>}</div>}

      <Dialog open={!!removing} onClose={() => setRemoving(null)} title={`Remove ${removing?.name ?? ""}?`}>
        {removing && (
          <div className="space-y-4">
            <p>{dayWithWeekday(removing.day)} stops being a {removing.kind === "floater" ? "floating holiday" : "public holiday"} for the {plan.people} {plan.people === 1 ? "person" : "people"} on {plan.name}. Requests already sent keep the days they were counted with.</p>
            {error && <ErrorNote message={error} />}
            <div className="flex justify-end gap-2">
              <button className="btn-quiet" onClick={() => setRemoving(null)}>Keep it</button>
              <button className="btn-danger" onClick={() => act(() => api(`/hr/holiday-plans/${id}/holidays/${removing.day}`, { method: "DELETE" }), () => setRemoving(null))}>Remove holiday</button>
            </div>
          </div>
        )}
      </Dialog>
      {dialog === "edit" && <EditPlanDialog plan={plan} onClose={() => setDialog("")} />}
      {dialog === "import" && <ImportDialog plan={plan} onClose={() => setDialog("")} />}
      {dialog === "assign" && <AssignDialog plan={plan} onClose={() => setDialog("")} />}
      {dialog === "delete" && <DeletePlanDialog plan={plan} onClose={() => setDialog("")} onDeleted={onDeleted} />}
    </section>
  );
}

function NewPlanDialog({ plans, onClose, onCreated }: { plans: HolidayPlan[]; onClose: () => void; onCreated: (id: number) => void }) {
  const [f, setF] = useState({ name: "", description: "", floater_quota: "2", copy_from: String(plans.find((p) => p.is_default)?.id ?? "") });
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault(); setError("");
    try {
      const r = await api<{ id: number }>("/hr/holiday-plans", { body: { ...f, floater_quota: Number(f.floater_quota), copy_from: f.copy_from ? Number(f.copy_from) : null } });
      await mutate("/hr/holiday-plans"); onCreated(r.id);
    } catch (err) { setError((err as Error).message); }
  }
  return (
    <Dialog open onClose={onClose} title="New holiday plan">
      <form onSubmit={submit} className="space-y-4">
        <label className="block"><span className="label">Name</span><input className="field" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="For example: Belgium office" /></label>
        <label className="block"><span className="label">Who it's for</span><input className="field" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block"><span className="label">Floating holidays a year</span><input className="field" type="number" min={0} max={10} value={f.floater_quota} onChange={(e) => setF({ ...f, floater_quota: e.target.value })} /></label>
          <label className="block"><span className="label">Start from</span>
            <select className="field" value={f.copy_from} onChange={(e) => setF({ ...f, copy_from: e.target.value })}>
              <option value="">An empty plan</option>
              {plans.map((p) => <option key={p.id} value={p.id}>A copy of {p.name}</option>)}
            </select></label>
        </div>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2"><button type="button" className="btn-quiet" onClick={onClose}>Cancel</button><button className="btn-primary">Create plan</button></div>
      </form>
    </Dialog>
  );
}

function EditPlanDialog({ plan, onClose }: { plan: HolidayPlan; onClose: () => void }) {
  const [f, setF] = useState({ name: plan.name, description: plan.description, floater_quota: String(plan.floater_quota) });
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault(); setError("");
    try { await api(`/hr/holiday-plans/${plan.id}`, { method: "PATCH", body: { ...f, floater_quota: Number(f.floater_quota) } }); refreshCalendars(); onClose(); }
    catch (err) { setError((err as Error).message); }
  }
  return (
    <Dialog open onClose={onClose} title={`Edit ${plan.name}`}>
      <form onSubmit={submit} className="space-y-4">
        <label className="block"><span className="label">Name</span><input className="field" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="block"><span className="label">Who it's for</span><input className="field" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        <label className="block max-w-[200px]"><span className="label">Floating holidays a year</span><input className="field" type="number" min={0} max={10} value={f.floater_quota} onChange={(e) => setF({ ...f, floater_quota: e.target.value })} /></label>
        <p className="text-sm text-ink-soft">Lowering it doesn't cancel floating holidays people have already chosen.</p>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2"><button type="button" className="btn-quiet" onClick={onClose}>Cancel</button><button className="btn-primary">Save</button></div>
      </form>
    </Dialog>
  );
}

function DeletePlanDialog({ plan, onClose, onDeleted }: { plan: HolidayPlan; onClose: () => void; onDeleted: () => void }) {
  const [error, setError] = useState("");
  async function del() {
    setError("");
    try { await api(`/hr/holiday-plans/${plan.id}`, { method: "DELETE" }); await mutate("/hr/holiday-plans"); onClose(); onDeleted(); }
    catch (err) { setError((err as Error).message); }
  }
  return (
    <Dialog open onClose={onClose} title={`Delete ${plan.name}?`}>
      <div className="space-y-4">
        <p>The plan and its {plan.holidays} holidays are deleted. Only a plan that nobody uses can be deleted.</p>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2"><button className="btn-quiet" onClick={onClose}>Keep it</button><button className="btn-danger" onClick={del}>Delete plan</button></div>
      </div>
    </Dialog>
  );
}

function AssignDialog({ plan, onClose }: { plan: HolidayPlan; onClose: () => void }) {
  const { data: people } = useSWR<Person[]>("/people", fetcher);
  const offices = Array.from(new Set((people ?? []).filter((p) => p.active).map((p) => p.location))).sort();
  const [location, setLocation] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ moved: number; already_on_plan: number } | null>(null);
  const count = (people ?? []).filter((p) => p.active && p.location === location).length;
  async function submit(e: FormEvent) {
    e.preventDefault(); setError("");
    try { setDone(await api(`/hr/holiday-plans/${plan.id}/assign`, { body: { location } })); refreshCalendars(); }
    catch (err) { setError((err as Error).message); }
  }
  return (
    <Dialog open onClose={onClose} title={`Move an office to ${plan.name}`}>
      {done ? (
        <div className="space-y-4">
          <p>{done.moved} {done.moved === 1 ? "person" : "people"} moved to {plan.name}{done.already_on_plan ? `; ${done.already_on_plan} were already on it` : ""}. Each move is in the person's change history.</p>
          <div className="flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <p className="text-ink-soft">Everyone currently in the office moves to this plan. To move one person, use the holiday plan on their profile.</p>
          <label className="block"><span className="label">Office</span>
            <select className="field" required value={location} onChange={(e) => setLocation(e.target.value)}>
              <option value="">Choose an office</option>
              {offices.map((o) => <option key={o} value={o}>{o}</option>)}
            </select></label>
          {location && <p className="text-sm">{count} {count === 1 ? "person" : "people"} in {location}.</p>}
          {error && <ErrorNote message={error} />}
          <div className="flex justify-end gap-2"><button type="button" className="btn-quiet" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={!location}>Move them</button></div>
        </form>
      )}
    </Dialog>
  );
}

const SAMPLE = "date,name,kind\n2027-06-21,Fête de la Musique,floater\n2027-07-16,Summer day off,mandatory";

function ImportDialog({ plan, onClose }: { plan: HolidayPlan; onClose: () => void }) {
  const [csv, setCsv] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(dry_run: boolean) {
    setBusy(true); setError("");
    try {
      const r = await api<ImportResult>(`/hr/holiday-plans/${plan.id}/import`, { body: { csv, dry_run } });
      setResult(r);
      if (!dry_run) refreshCalendars();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    setCsv(await file.text()); setResult(null);
  }

  return (
    <Dialog open onClose={onClose} title={`Import holidays into ${plan.name}`} width="max-w-2xl">
      {result && !result.dry_run ? (
        <div className="space-y-4">
          <p>{result.added} {result.added === 1 ? "holiday" : "holidays"} added{result.skipped ? `, ${result.skipped} skipped` : ""}.</p>
          <div className="flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-ink-soft">A CSV file with the columns <span className="font-medium text-ink">date, name, kind</span>. Kind is optional: public holiday unless it says floater. Nothing is saved until you've checked the preview.</p>
          <label className="block"><span className="label">File</span>
            <input type="file" accept=".csv,text/csv" className="block text-sm" onChange={(e) => readFile(e.target.files?.[0])} /></label>
          <label className="block"><span className="label">Or paste the rows</span>
            <textarea className="field h-32 py-2 font-[inherit]" value={csv} placeholder={SAMPLE} onChange={(e) => { setCsv(e.target.value); setResult(null); }} /></label>
          {result && (
            <div className="max-h-56 overflow-y-auto rounded border border-rule">
              <table className="w-full text-sm">
                <tbody className="divide-y divide-rule">
                  {result.rows.map((r) => (
                    <tr key={r.line}>
                      <td className="px-3 py-1.5 text-ink-soft">Line {r.line}</td>
                      <td className="px-3 py-1.5">{r.name || "(no name)"}</td>
                      <td className="px-3 py-1.5 whitespace-nowrap">{r.date}</td>
                      <td className={`px-3 py-1.5 ${r.ok ? "text-ok" : "text-no"}`}>{r.ok ? (r.kind === "floater" ? "Will add, floating" : "Will add") : r.problem}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {error && <ErrorNote message={error} />}
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-ink-soft">{result ? `${result.will_add} to add, ${result.skipped} with problems` : ""}</p>
            <div className="flex gap-2">
              <button className="btn-quiet" onClick={onClose}>Cancel</button>
              {result && result.will_add ? (
                <button className="btn-primary" disabled={busy} onClick={() => run(false)}>Import {result.will_add} {result.will_add === 1 ? "holiday" : "holidays"}</button>
              ) : (
                <button className="btn-primary" disabled={busy || !csv.trim()} onClick={() => run(true)}>Check the file</button>
              )}
            </div>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function YearStart() {
  const nextYear = new Date().getFullYear() + 1;
  const { data: preview } = useSWR<Preview>(`/hr/leave/year-start?year=${nextYear}`, fetcher);
  return (
    <section aria-labelledby="yearly" className="panel max-w-xl px-5 py-5">
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
            <Row label="Paid days carried over" value={preview.paid_days_carried} hint="Paid leave is earned monthly; unused days carry over in full" />
            {preview.paid_days_lapsing > 0 && <Row label="Paid days that lapse" value={preview.paid_days_lapsing} />}
          </dl>
          <p className="mt-4 rounded border border-rule bg-paper px-3 py-2 text-sm">Then everyone gets 12 casual and 6 sick days for {preview.year}, and a new set of floating holidays from their plan. Paid leave isn't topped up in January: it is earned at 2.5 days a month. Each change appears in the person's balance history.</p>
        </>
      )}
    </section>
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
