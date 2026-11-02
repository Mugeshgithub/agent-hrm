import { money, monthName } from "@/lib/format";
import { Payslip, Person } from "@/lib/types";

// laid out like a French bulletin de paie: items, then employee and employer amounts, totals at the foot
export function PayslipDoc({ slip, person }: { slip: Payslip; person: Person }) {
  const earnings = slip.lines.filter((l) => l.kind === "earning");
  const contributions = slip.lines.filter((l) => l.kind === "employee_contribution");
  const employer = slip.lines.filter((l) => l.kind === "employer_contribution");
  const tax = slip.lines.filter((l) => l.kind === "tax");

  return (
    <article className="payslip panel overflow-hidden">
      <header className="grid sm:grid-cols-2 gap-4 border-b border-rule px-5 py-5">
        <div>
          <p className="text-sm text-ink-soft">Payslip</p>
          <h2 className="text-lg font-semibold">{monthName(slip.month)}</h2>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm sm:justify-self-end">
          <dt className="text-ink-soft">Employee</dt><dd className="font-medium">{person.name}</dd>
          <dt className="text-ink-soft">Position</dt><dd>{person.title}</dd>
          <dt className="text-ink-soft">Employee ID</dt><dd>{person.id}</dd>
        </dl>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px]">
          <thead>
            <tr className="border-b border-rule">
              <th className="table-head px-5 py-2.5">Item</th>
              <th className="table-head px-5 py-2.5 text-right">Employee</th>
              <th className="table-head px-5 py-2.5 text-right">Employer</th>
            </tr>
          </thead>
          <tbody>
            {earnings.map((l) => (
              <tr key={l.label} className="border-b border-rule/70">
                <td className="px-5 py-2">{l.label}</td>
                <td className="px-5 py-2 text-right">{money(l.amount_eur)}</td>
                <td className="px-5 py-2" />
              </tr>
            ))}
            <tr className="border-b border-rule bg-paper/70 font-medium">
              <td className="px-5 py-2">Gross pay</td><td className="px-5 py-2 text-right">{slip.gross_eur}</td><td />
            </tr>
            {contributions.map((l) => (
              <tr key={l.label} className="border-b border-rule/70">
                <td className="px-5 py-2">{l.label}</td>
                <td className="px-5 py-2 text-right">{money(l.amount_eur, true)}</td>
                <td className="px-5 py-2" />
              </tr>
            ))}
            {employer.map((l) => (
              <tr key={l.label} className="border-b border-rule/70 text-ink-soft">
                <td className="px-5 py-2">{l.label}</td>
                <td className="px-5 py-2" />
                <td className="px-5 py-2 text-right">{l.amount_eur}</td>
              </tr>
            ))}
            <tr className="border-b border-rule bg-paper/70 font-medium">
              <td className="px-5 py-2">Net before tax</td><td className="px-5 py-2 text-right">{slip.net_before_tax_eur}</td><td />
            </tr>
            {tax.map((l) => (
              <tr key={l.label} className="border-b border-rule/70">
                <td className="px-5 py-2">{l.label}</td>
                <td className="px-5 py-2 text-right">{money(l.amount_eur, true)}</td>
                <td className="px-5 py-2" />
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td className="px-5 py-4 text-md font-semibold">Net paid</td>
              <td className="px-5 py-4 text-right text-lg font-semibold">{slip.net_paid_eur}</td>
              <td className="px-5 py-4 text-right text-sm text-ink-soft">Employer total {slip.employer_contributions_eur}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </article>
  );
}
