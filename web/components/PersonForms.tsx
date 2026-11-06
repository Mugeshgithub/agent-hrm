"use client";
// HR forms for employee records: add a person, edit their details, record that they're leaving.
// The rules live in employees.py; these forms only collect the answers and show the server's reason
// when something is refused.
import { FormEvent, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { api, fetcher } from "@/lib/api";
import { day } from "@/lib/format";
import { Person, Profile } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";

export const ROLE: Record<string, string> = { employee: "Employee", manager: "Manager", hr: "HR" };
const CONTRACTS = ["CDI", "CDD", "Apprenticeship", "Internship"];
export const CONTRACT: Record<string, string> = {
  CDI: "Permanent (CDI)", CDD: "Fixed-term (CDD)", Apprenticeship: "Apprenticeship", Internship: "Internship",
};

type Fields = {
  name: string; email: string; role: string; title: string; department: string; location: string;
  manager_id: string; start_date: string; contract_type: string;
};

const EMPTY: Fields = {
  name: "", email: "", role: "employee", title: "", department: "", location: "Paris",
  manager_id: "", start_date: "", contract_type: "CDI",
};

function usePeopleOptions() {
  const { data } = useSWR<Person[]>("/people", fetcher);
  const people = (data ?? []).filter((p) => p.active);
  const unique = (xs: string[]) => Array.from(new Set(xs.filter(Boolean))).sort();
  return {
    managers: people.filter((p) => p.role === "manager"),
    departments: unique(people.map((p) => p.department)),
    locations: unique(people.map((p) => p.location)),
  };
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-sm text-ink-soft">{hint}</span>}
    </label>
  );
}

function DetailFields({ f, set, editing, selfId }:
  { f: Fields; set: (k: keyof Fields, v: string) => void; editing: boolean; selfId?: string }) {
  const { managers, departments, locations } = usePeopleOptions();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Full name"><input className="field" required value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
      <Field label="Work email"><input className="field" type="email" required value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>
      <Field label="Job title"><input className="field" required value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
      <Field label="Department">
        <input className="field" required list="departments" value={f.department} onChange={(e) => set("department", e.target.value)} />
        <datalist id="departments">{departments.map((d) => <option key={d} value={d} />)}</datalist>
      </Field>
      <Field label="Location">
        <input className="field" required list="locations" value={f.location} onChange={(e) => set("location", e.target.value)} />
        <datalist id="locations">{locations.map((d) => <option key={d} value={d} />)}</datalist>
      </Field>
      <Field label="Reports to">
        <select className="field" value={f.manager_id} onChange={(e) => set("manager_id", e.target.value)}>
          <option value="">No one (top of the company)</option>
          {managers.filter((m) => m.id !== selfId).map((m) => <option key={m.id} value={m.id}>{m.name}, {m.title}</option>)}
        </select>
      </Field>
      <Field label="Role in this app" hint="Managers approve leave. HR manages records and requests.">
        <select className="field" value={f.role} onChange={(e) => set("role", e.target.value)}>
          {Object.entries(ROLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
      <Field label="Contract">
        <select className="field" value={f.contract_type} onChange={(e) => set("contract_type", e.target.value)}>
          {CONTRACTS.map((c) => <option key={c} value={c}>{CONTRACT[c]}</option>)}
        </select>
      </Field>
      {!editing && (
        <Field label="Start date" hint="They start with 12 casual and 6 sick days, as in the policy. Paid leave is earned at 2.5 days a month.">
          <input className="field" type="date" required value={f.start_date} onChange={(e) => set("start_date", e.target.value)} />
        </Field>
      )}
    </div>
  );
}

export function AddPersonDialog({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: (id: string) => void }) {
  const { mutate } = useSWRConfig();
  const [f, setF] = useState<Fields>(EMPTY);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Fields, v: string) => setF((x) => ({ ...x, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<{ id: string }>("/hr/people", { body: { ...f, manager_id: f.manager_id || null } });
      await mutate("/people");
      setF(EMPTY);
      onAdded(r.id);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Add a person" width="max-w-2xl">
      <form onSubmit={submit} className="space-y-5">
        <DetailFields f={f} set={set} editing={false} />
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? "Adding…" : "Add person"}</button>
        </div>
      </form>
    </Dialog>
  );
}

export function EditPersonDialog({ person, open, onClose }: { person: Profile; open: boolean; onClose: () => void }) {
  const { mutate } = useSWRConfig();
  const start: Fields = {
    name: person.name, email: person.email, role: person.role, title: person.title, department: person.department,
    location: person.location, manager_id: person.manager_id ?? "", start_date: person.start_date,
    contract_type: person.contract_type ?? "CDI",
  };
  const [f, setF] = useState<Fields>(start);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof Fields, v: string) => setF((x) => ({ ...x, [k]: v }));
  const changed = (Object.keys(start) as (keyof Fields)[]).filter((k) => k !== "start_date" && f[k].trim() !== start[k]);

  function close() { setF(start); setReason(""); setError(""); onClose(); }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const changes = Object.fromEntries(changed.map((k) => [k, k === "manager_id" ? f[k] || null : f[k]]));
    try {
      await api(`/hr/people/${person.id}`, { method: "PATCH", body: { changes, reason } });
      await Promise.all([mutate(`/people/${person.id}`), mutate(`/people/${person.id}/history`), mutate("/people")]);
      setReason("");
      onClose();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onClose={close} title={`Edit ${person.name}'s details`} width="max-w-2xl">
      <form onSubmit={submit} className="space-y-5">
        <DetailFields f={f} set={set} editing selfId={person.id} />
        <Field label="Reason for the change" hint="Saved in the change history with your name.">
          <input className="field" required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: promotion, moved to the Lyon office" />
        </Field>
        {error && <ErrorNote message={error} />}
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-ink-soft">{changed.length === 0 ? "No changes yet." : `${changed.length} ${changed.length === 1 ? "change" : "changes"} to save.`}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-quiet" onClick={close}>Cancel</button>
            <button className="btn-primary" disabled={busy || changed.length === 0}>{busy ? "Saving…" : "Save changes"}</button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}

type Offboarded = { end_date: string; final_balance: { casual: number; sick: number; paid: number } };

export function OffboardDialog({ person, open, onClose }: { person: Profile; open: boolean; onClose: () => void }) {
  const { mutate } = useSWRConfig();
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Offboarded | null>(null);

  function close() { setEndDate(""); setReason(""); setError(""); setDone(null); onClose(); }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<Offboarded>(`/hr/people/${person.id}/offboard`, { body: { end_date: endDate, reason } });
      await Promise.all([mutate(`/people/${person.id}`), mutate(`/people/${person.id}/history`), mutate("/people")]);
      setDone(r);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onClose={close} title={done ? `${person.name} is leaving` : `Record that ${person.name} is leaving`}>
      {done ? (
        <div className="space-y-4">
          <p>Their last day is <span className="font-medium">{day(done.end_date)} {done.end_date.slice(0, 4)}</span>. After that they can't sign in, and they leave their team. Their records and payslips are kept.</p>
          <div className="rounded border border-rule bg-paper px-4 py-3">
            <p className="text-sm text-ink-soft">For the final payslip</p>
            <p className="mt-0.5">{done.final_balance.casual} unused casual {done.final_balance.casual === 1 ? "day" : "days"} to pay out</p>
            <p className="mt-0.5">{done.final_balance.paid} unused paid leave {done.final_balance.paid === 1 ? "day" : "days"} to pay out (indemnité compensatrice)</p>
          </div>
          <div className="flex justify-end"><button className="btn-primary" onClick={close}>Done</button></div>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <p className="text-ink-soft">Nothing is deleted. {person.name} keeps access until the end of their last day.</p>
          {person.reports.length > 0 && (
            <ErrorNote message={`${person.name} manages ${person.reports.length} ${person.reports.length === 1 ? "person" : "people"}. Give them a new manager first, from each person's profile.`} />
          )}
          <Field label="Last day"><input className="field" type="date" required value={endDate} onChange={(e) => setEndDate(e.target.value)} /></Field>
          <Field label="Reason" hint="Saved in the change history with your name.">
            <input className="field" required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: resigned, end of fixed-term contract" />
          </Field>
          {error && <ErrorNote message={error} />}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-quiet" onClick={close}>Cancel</button>
            <button className="btn-danger" disabled={busy || person.reports.length > 0}>{busy ? "Saving…" : "Record leaving date"}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}


export function AdjustBalanceDialog({ person, onClose }: { person: Profile; onClose: () => void }) {
  const { mutate } = useSWRConfig();
  const [leaveType, setLeaveType] = useState<"casual" | "sick" | "paid">("casual");
  const [direction, setDirection] = useState<"add" | "remove">("add");
  const [days, setDays] = useState("1");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = person.balances?.[leaveType].balance ?? 0;
  const step = leaveType === "paid" ? 0.5 : 1;               // paid leave moves in half days
  const n = Math.max(0, Math.floor((Number(days) || 0) / step) * step);
  const change = direction === "add" ? n : -n;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/hr/people/${person.id}/balance`, { body: { leave_type: leaveType, days: change, reason } });
      await Promise.all([mutate(`/people/${person.id}`), mutate(`/hr/people/${person.id}/leave-history`), mutate("/people")]);
      onClose();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title={`Adjust ${person.name}'s balance`}>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Leave type">
            <select className="field" value={leaveType} onChange={(e) => setLeaveType(e.target.value as "casual" | "sick" | "paid")}>
              <option value="casual">Casual</option><option value="sick">Sick</option><option value="paid">Paid</option>
            </select>
          </Field>
          <Field label="Change">
            <select className="field" value={direction} onChange={(e) => setDirection(e.target.value as "add" | "remove")}>
              <option value="add">Add days</option><option value="remove">Remove days</option>
            </select>
          </Field>
          <Field label="Days"><input className="field" type="number" min={step} step={step} max={30} required value={days} onChange={(e) => setDays(e.target.value)} /></Field>
        </div>
        <p className="text-sm">Balance goes from <span className="font-medium">{current}</span> to <span className="font-medium">{current + change}</span> {leaveType} days.</p>
        <Field label="Reason" hint={`${person.name} sees this in their balance history.`}>
          <input className="field" required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: worked on a public holiday" />
        </Field>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || n === 0}>{busy ? "Saving…" : "Save adjustment"}</button>
        </div>
      </form>
    </Dialog>
  );
}
