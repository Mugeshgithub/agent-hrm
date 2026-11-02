"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Printer } from "lucide-react";
import { fetcher } from "@/lib/api";
import { monthName } from "@/lib/format";
import { Payslip } from "@/lib/types";
import { Empty, PageHeader } from "@/components/PageHeader";
import { PayslipDoc } from "@/components/PayslipDoc";
import { useMe } from "@/components/Me";

type Item = { month: string; net_paid_eur: string; gross_eur: string };

const cents = (eur: string) => Math.round(parseFloat(eur.replace(/[^0-9.-]/g, "")) * 100);
const euros = (c: number) => `€${(Math.abs(c) / 100).toLocaleString("en-GB", { minimumFractionDigits: 2 })}`;

export default function Payslips() {
  const me = useMe();
  const { data: list } = useSWR<Item[]>("/payslips", fetcher);
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => { if (list?.length && !month) setMonth(list[0].month); }, [list, month]);
  const { data: slip } = useSWR<Payslip>(month ? `/payslips/${month}` : null, fetcher);

  const i = list?.findIndex((x) => x.month === month) ?? -1;
  const prev = list && i >= 0 ? list[i + 1] : undefined;
  const change = list && prev && i >= 0 ? cents(list[i].net_paid_eur) - cents(prev.net_paid_eur) : null;

  return (
    <>
      <PageHeader title="Payslips" description="Issued on the last working day of each month. Amounts come straight from payroll."
                  actions={slip && <button className="btn-quiet no-print" onClick={() => window.print()}><Printer size={16} aria-hidden />Print or save as PDF</button>} />
      {list && list.length === 0 && <div className="panel"><Empty title="No payslips yet">Your first one appears here on the last working day of the month.</Empty></div>}
      {list && list.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <nav aria-label="Months" className="no-print">
            <ul className="panel divide-y divide-rule overflow-hidden">
              {list.map((p) => (
                <li key={p.month}>
                  <button onClick={() => setMonth(p.month)} aria-current={p.month === month ? "true" : undefined}
                          className={`w-full px-4 py-3 text-left ${p.month === month ? "bg-dossier-tint" : "hover:bg-paper"}`}>
                    <span className={`block ${p.month === month ? "font-medium text-dossier" : ""}`}>{monthName(p.month)}</span>
                    <span className="block text-sm text-ink-soft">{p.net_paid_eur} net</span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <div>
            {change !== null && prev && (
              <p className="mb-3 text-ink-soft no-print">
                {change === 0 ? `Same net pay as ${monthName(prev.month)}.` :
                  `${euros(change)} ${change > 0 ? "more" : "less"} than ${monthName(prev.month)}.`}
              </p>
            )}
            {slip && <PayslipDoc slip={slip} person={me} />}
          </div>
        </div>
      )}
    </>
  );
}
