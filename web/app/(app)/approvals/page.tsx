"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { Pending } from "@/lib/types";
import { LeaveDecision } from "@/components/LeaveDecision";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";
import { useMe } from "@/components/Me";

export default function Approvals() {
  const me = useMe();
  const { data, error } = useSWR<Pending[]>(me.role === "manager" ? "/approvals" : null, fetcher);
  if (me.role !== "manager") return <Empty title="Approvals are for managers">Your requests go to your manager, {me.manager_name}.</Empty>;

  return (
    <>
      <PageHeader title="Approvals" description="Leave requests from your team, oldest first. Each one shows who else is off at the same time." />
      {error && <ErrorNote message={error.message} />}
      {data && data.length === 0 && <div className="panel"><Empty title="You're all caught up">New requests from your team will appear here.</Empty></div>}
      <ul className="space-y-4">
        {data?.map((p) => <LeaveDecision key={p.request_id} p={p} />)}
      </ul>
    </>
  );
}
