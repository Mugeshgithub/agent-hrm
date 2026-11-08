"use client";
// The public list of open jobs. No login: anyone can read it.
import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { PublicJob } from "@/lib/types";
import { CareersFrame } from "@/components/CareersFrame";
import { salaryText } from "@/components/JobParts";
import { ErrorNote } from "@/components/PageHeader";

export default function Careers() {
  const { data, error } = useSWR<PublicJob[]>("/careers/jobs", fetcher);
  return (
    <CareersFrame>
      <h1 className="text-xl font-semibold tracking-[-0.01em]">Open jobs</h1>
      <p className="mt-1 mb-6 text-ink-soft">Read a job and apply from its page. Every application is read by a person.</p>
      {error && <ErrorNote message={error.message} />}
      {data && data.length === 0 && <div className="panel px-5 py-10 text-center"><p className="font-medium">No open jobs right now</p><p className="mt-1 text-ink-soft">Please check back later.</p></div>}
      {!!data?.length && (
        <ul className="panel divide-y divide-rule">
          {data.map((j) => (
            <li key={j.id}>
              <Link href={`/careers/${j.id}`} className="block px-5 py-4 hover:bg-paper">
                <span className="block font-medium text-dossier">{j.title}</span>
                <span className="block text-sm text-ink-soft">{j.team}, {j.location}, {j.contract_type}{salaryText(j) ? `, ${salaryText(j)}` : ""}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </CareersFrame>
  );
}
