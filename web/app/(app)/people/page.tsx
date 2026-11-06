"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Plus, Search } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { day } from "@/lib/format";
import { Payslip, Person } from "@/lib/types";
import { Avatar } from "@/components/Shell";
import { Dialog } from "@/components/Dialog";
import { ErrorNote, PageHeader } from "@/components/PageHeader";
import { PayslipDoc } from "@/components/PayslipDoc";
import { useMe } from "@/components/Me";
import { AddPersonDialog, ROLE } from "@/components/PersonForms";

export default function People() {
  const me = useMe();
  const { data } = useSWR<Person[]>("/people", fetcher);
  const [q, setQ] = useState("");
  const [viewing, setViewing] = useState<Person | null>(null);
  const [adding, setAdding] = useState(false);
  const [showLeft, setShowLeft] = useState(false);
  const router = useRouter();
  const isHR = me.role === "hr";
  const formerCount = (data ?? []).filter((p) => !p.active).length;
  const rows = (data ?? [])
    .filter((p) => p.active || showLeft)
    .filter((p) => `${p.name} ${p.title} ${p.department} ${p.location}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <PageHeader title="People"
        description={isHR ? "Everyone at the company. Leave balances and payslips are visible to HR only, and every payslip you open is logged." : "Who's who, and who they report to."}
        actions={isHR && <button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} aria-hidden /> Add person</button>} />
      <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-3">
        <div className="relative w-full max-w-sm">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" aria-hidden />
          <input className="field pl-9" placeholder="Search by name, team or city" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
        </div>
        <p className="text-sm text-ink-soft">{rows.length} {rows.length === 1 ? "person" : "people"}</p>
        {isHR && formerCount > 0 && (
          <label className="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-dossier" checked={showLeft} onChange={(e) => setShowLeft(e.target.checked)} />
            Show people who have left ({formerCount})
          </label>
        )}
      </div>
      <section className="panel overflow-x-auto">
        <table className="w-full min-w-[680px]">
          <thead>
            <tr className="border-b border-rule">
              <th className="table-head px-5 py-2.5">Name</th>
              <th className="table-head px-3 py-2.5">Team</th>
              <th className="table-head px-3 py-2.5">Reports to</th>
              <th className="table-head px-3 py-2.5">Since</th>
              {isHR && <th className="table-head px-3 py-2.5 text-right">Casual / sick</th>}
              {isHR && <th className="table-head px-5 py-2.5"><span className="sr-only">Actions</span></th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {rows.map((p) => (
              <tr key={p.id}>
                <td className="px-5 py-3">
                  <span className="flex items-center gap-3">
                    <Avatar name={p.name} size={32} />
                    <span><Link href={`/people/${p.id}`} className="block font-medium hover:text-dossier hover:underline">{p.name}{p.id === me.id && <span className="ml-1.5 font-normal text-ink-soft">(you)</span>}</Link>
                      <span className="block text-sm text-ink-soft">{p.title}{!p.active && p.end_date && `, left ${day(p.end_date)} ${p.end_date.slice(0, 4)}`}</span></span>
                  </span>
                </td>
                <td className="px-3 py-3"><span className="block">{p.department}</span><span className="block text-sm text-ink-soft">{p.location}, {ROLE[p.role]}</span></td>
                <td className="px-3 py-3">{p.manager_name ?? <span className="text-ink-faint">None</span>}</td>
                <td className="px-3 py-3 whitespace-nowrap">{day(p.start_date)} {p.start_date.slice(0, 4)}</td>
                {isHR && <td className="px-3 py-3 text-right">{p.casual_balance} / {p.sick_balance}</td>}
                {isHR && <td className="px-5 py-3 text-right">{p.id !== me.id && <button className="text-sm text-dossier hover:underline whitespace-nowrap" onClick={() => setViewing(p)}>Open payslip</button>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {isHR && <PayslipViewer person={viewing} onClose={() => setViewing(null)} />}
      {isHR && <AddPersonDialog open={adding} onClose={() => setAdding(false)} onAdded={(id) => { setAdding(false); router.push(`/people/${id}`); }} />}
    </>
  );
}

function PayslipViewer({ person, onClose }: { person: Person | null; onClose: () => void }) {
  const [month, setMonth] = useState(() => {                 // last month: the latest issued payslip
    const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
  const [slip, setSlip] = useState<Payslip | null>(null);
  const [error, setError] = useState("");

  async function open() {
    if (!person) return;
    setError("");
    setSlip(null);
    try { setSlip(await api<Payslip>(`/hr/payslips/${person.id}/${month}`)); }
    catch (e) { setError((e as Error).message); }
  }

  return (
    <Dialog open={!!person} onClose={() => { setSlip(null); setError(""); onClose(); }} title={person ? `${person.name}'s payslip` : ""} width="max-w-3xl">
      {person && !slip && (
        <div className="space-y-4">
          <p className="text-ink-soft">Opening someone's payslip is recorded in the payslip access log, with your name and the time.</p>
          <label className="block max-w-xs"><span className="label">Month</span>
            <input type="month" className="field" value={month} onChange={(e) => setMonth(e.target.value)} /></label>
          {error && <ErrorNote message={error} />}
          <div className="flex justify-end gap-2">
            <button className="btn-quiet" onClick={onClose}>Cancel</button>
            <button className="btn-primary" onClick={open}>Open and log access</button>
          </div>
        </div>
      )}
      {person && slip && <div className="max-h-[70vh] overflow-y-auto"><PayslipDoc slip={slip} person={person} /></div>}
    </Dialog>
  );
}
