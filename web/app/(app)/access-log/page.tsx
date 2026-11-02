"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Empty, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

type Entry = { id: number; viewer_name: string; employee_name: string; emp_id: string; month: string; viewed_at: string };

export default function AccessLog() {
  const me = useMe();
  const { data } = useSWR<Entry[]>(me.role === "hr" ? "/hr/access-log" : null, fetcher);
  if (me.role !== "hr") return <Empty title="The access log is for HR" />;
  return (
    <>
      <PageHeader title="Payslip access" description="Every time someone in HR opens another person's payslip, here or through the assistant. The app has no way to edit or delete entries." />
      <section className="panel overflow-x-auto">
        {data && data.length === 0 ? <Empty title="No one has opened another person's payslip yet" /> : (
          <table className="w-full min-w-[560px]">
            <thead><tr className="border-b border-rule">
              <th className="table-head px-5 py-2.5">When</th><th className="table-head px-3 py-2.5">Opened by</th>
              <th className="table-head px-3 py-2.5">Whose payslip</th><th className="table-head px-5 py-2.5">Period</th>
            </tr></thead>
            <tbody className="divide-y divide-rule">
              {data?.map((e) => (
                <tr key={e.id}>
                  <td className="px-5 py-2.5 whitespace-nowrap">{timestamp(e.viewed_at)}</td>
                  <td className="px-3 py-2.5">{e.viewer_name}</td>
                  <td className="px-3 py-2.5">{e.employee_name} <span className="text-ink-soft">{e.emp_id}</span></td>
                  <td className="px-5 py-2.5">{e.month}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
