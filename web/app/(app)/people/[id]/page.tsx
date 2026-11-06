"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";
import useSWR from "swr";
import { ArrowLeft } from "lucide-react";
import { fetcher } from "@/lib/api";
import { day, daysUntil, timestamp } from "@/lib/format";
import { Change, Person, Profile } from "@/lib/types";
import { Avatar } from "@/components/Shell";
import { Empty } from "@/components/PageHeader";
import { CONTRACT, EditPersonDialog, OffboardDialog, ROLE } from "@/components/PersonForms";
import { useMe } from "@/components/Me";

const FIELD: Record<string, string> = {
  name: "Name", email: "Email", role: "Role", manager_id: "Manager", title: "Job title",
  department: "Department", location: "Location", contract_type: "Contract", end_date: "Last day",
};

function fullDate(iso: string) {
  return `${day(iso)} ${iso.slice(0, 4)}`;
}

function tenure(start: string) {
  const ahead = daysUntil(start);
  if (ahead > 0) return ahead === 1 ? "starts tomorrow" : `starts in ${ahead} days`;
  const months = Math.floor(-daysUntil(start) / 30.44);
  if (months < 1) return "joined this month";
  if (months < 12) return `${months} ${months === 1 ? "month" : "months"}`;
  const years = Math.floor(months / 12);
  return `${years} ${years === 1 ? "year" : "years"}${months % 12 ? `, ${months % 12} months` : ""}`;
}

export default function ProfilePage({ params }: { params: { id: string } }) {
  const me = useMe();
  const isHR = me.role === "hr";
  const { data: p, error } = useSWR<Profile>(`/people/${params.id}`, fetcher);
  const canSeeHistory = isHR || me.id === params.id;
  const { data: history } = useSWR<{ changes: Change[] }>(canSeeHistory ? `/people/${params.id}/history` : null, fetcher);
  const { data: everyone } = useSWR<Person[]>(canSeeHistory ? "/people" : null, fetcher)   // names for "Manager: X to Y";
  const [editing, setEditing] = useState(false);
  const [leaving, setLeaving] = useState(false);

  if (error) return <Empty title="We couldn't find this person"><Link href="/people" className="text-dossier hover:underline">Back to People</Link></Empty>;
  if (!p) return <p className="text-ink-soft">Loading…</p>;

  const nameOf = (id: string | null) => (id ? everyone?.find((x) => x.id === id)?.name ?? id : "No one");
  const leftAlready = !p.active;

  return (
    <>
      <Link href="/people" className="mb-5 inline-flex items-center gap-1.5 text-sm text-ink-soft hover:text-ink">
        <ArrowLeft size={14} aria-hidden /> People
      </Link>

      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <Avatar name={p.name} size={56} />
          <div>
            <h1 className="text-xl font-semibold tracking-[-0.01em]">{p.name}{p.id === me.id && <span className="ml-2 text-md font-normal text-ink-soft">(you)</span>}</h1>
            <p className="text-ink-soft">{p.title}, {p.department}</p>
            {p.end_date && (
              <p className="mt-1 inline-flex items-center gap-1.5 text-sm">
                <span className="h-2 w-2 rounded-full bg-off" aria-hidden />
                {leftAlready ? `Left on ${fullDate(p.end_date)}` : `Leaving on ${fullDate(p.end_date)}`}
              </p>
            )}
          </div>
        </div>
        {isHR && !leftAlready && (
          <div className="flex gap-2">
            <button className="btn-quiet" onClick={() => setEditing(true)}>Edit details</button>
            {!p.end_date && p.id !== me.id && <button className="btn-danger" onClick={() => setLeaving(true)}>Record leaving</button>}
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-6">
          <section aria-labelledby="details" className="panel">
            <h2 id="details" className="px-5 pt-5 font-medium">Details</h2>
            <dl className="grid gap-x-6 gap-y-4 px-5 py-5 sm:grid-cols-2">
              <Item label="Work email"><a href={`mailto:${p.email}`} className="text-dossier hover:underline break-all">{p.email}</a></Item>
              <Item label="Reports to">
                {p.manager_id ? <Link href={`/people/${p.manager_id}`} className="text-dossier hover:underline">{p.manager_name}</Link> : "No one"}
              </Item>
              <Item label="Department">{p.department}</Item>
              <Item label="Location">{p.location}</Item>
              <Item label="Started">{fullDate(p.start_date)} <span className="text-ink-soft">({tenure(p.start_date)})</span></Item>
              <Item label="Role in this app">{ROLE[p.role]}</Item>
              {isHR && <Item label="Contract">{CONTRACT[p.contract_type ?? "CDI"] ?? p.contract_type}</Item>}
              {isHR && <Item label="Employee ID">{p.id}</Item>}
            </dl>
          </section>

          {canSeeHistory && (
            <section aria-labelledby="history" className="panel">
              <div className="px-5 pt-5">
                <h2 id="history" className="font-medium">Change history</h2>
                <p className="text-sm text-ink-soft">Every change HR makes to this record, with who made it and why.</p>
              </div>
              {!history ? <p className="px-5 py-5 text-ink-soft">Loading…</p> : history.changes.length === 0 ? (
                <p className="px-5 py-5 text-ink-soft">No changes since this record was set up.</p>
              ) : (
                <ol className="mt-3 divide-y divide-rule border-t border-rule">
                  {history.changes.map((c, i) => (
                    <li key={i} className="px-5 py-3">
                      <p>{describe(c, nameOf)}</p>
                      <p className="text-sm text-ink-soft">
                        {c.reason && <>&ldquo;{c.reason}&rdquo;, </>}{c.changed_by ?? "Unknown"}, {timestamp(c.changed_at)}
                      </p>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
        </div>

        <div className="space-y-6">
          <section aria-labelledby="reports" className="panel">
            <h2 id="reports" className="px-5 pt-5 font-medium">
              {p.reports.length ? `${p.reports.length} ${p.reports.length === 1 ? "person reports" : "people report"} to ${p.id === me.id ? "you" : p.name.split(" ")[0]}` : "Direct reports"}
            </h2>
            {p.reports.length === 0 ? <p className="px-5 pb-5 pt-1 text-ink-soft">No one.</p> : (
              <ul className="mt-2 divide-y divide-rule border-t border-rule">
                {p.reports.map((r) => (
                  <li key={r.id}>
                    <Link href={`/people/${r.id}`} className="flex items-center gap-3 px-5 py-2.5 hover:bg-paper/60">
                      <Avatar name={r.name} size={28} />
                      <span><span className="block font-medium">{r.name}</span><span className="block text-sm text-ink-soft">{r.title}</span></span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {isHR && p.balances && (
            <section aria-labelledby="balances" className="panel px-5 py-5">
              <h2 id="balances" className="font-medium">Leave balance</h2>
              <dl className="mt-3 grid grid-cols-2 gap-4">
                {(["casual", "sick"] as const).map((t) => (
                  <div key={t}>
                    <dt className="text-sm text-ink-soft">{t === "casual" ? "Casual" : "Sick"}</dt>
                    <dd><span className="text-lg font-semibold">{p.balances![t].available}</span>
                      <span className="ml-1 text-ink-soft">available</span></dd>
                    {p.balances![t].pending > 0 && <dd className="text-sm text-ink-soft">{p.balances![t].pending} waiting for approval</dd>}
                  </div>
                ))}
              </dl>
            </section>
          )}
        </div>
      </div>

      {isHR && editing && <EditPersonDialog person={p} open onClose={() => setEditing(false)} />}
      {isHR && leaving && <OffboardDialog person={p} open onClose={() => setLeaving(false)} />}
    </>
  );
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-ink-soft">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function describe(c: Change, nameOf: (id: string | null) => string) {
  if (c.field === "record") return "Record created";
  if (c.field === "end_date") return `Last day set to ${c.new_value ? fullDate(c.new_value) : "none"}`;
  const show = (v: string | null) => {
    if (c.field === "manager_id") return nameOf(v);
    if (c.field === "role") return v ? ROLE[v] ?? v : "None";
    if (c.field === "contract_type") return v ? CONTRACT[v] ?? v : "None";
    return v || "None";
  };
  return `${FIELD[c.field] ?? c.field}: ${show(c.old_value)} to ${show(c.new_value)}`;
}
