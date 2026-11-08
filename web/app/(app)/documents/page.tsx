"use client";
// My documents: the contract, amendments and certificates HR has filed for this person, and who opened each. Only the
// person and HR can open them; every opening is logged and shown here. The assistant can answer questions about them.
import useSWR from "swr";
import { FileText } from "lucide-react";
import { fetcher } from "@/lib/api";
import { day, timestamp } from "@/lib/format";
import { PersonFile } from "@/lib/types";
import { Empty, ErrorNote, PageHeader } from "@/components/PageHeader";

const viewsLine = (f: PersonFile) =>
  f.views.length === 0 ? "Not opened yet" : `Opened ${f.views.length === 5 ? "at least " : ""}${f.views.length} ${f.views.length === 1 ? "time" : "times"}, last by ${f.views[0].viewer_name ?? "someone"}${f.views[0].via === "assistant" ? " (the assistant, answering you)" : ""} on ${timestamp(f.views[0].viewed_at)}`;

export default function DocumentsPage() {
  const { data, error } = useSWR<PersonFile[]>("/files", fetcher);
  return (
    <>
      <PageHeader title="My documents"
        description="Your contract, amendments and certificates, filed by HR. Only you and HR can open them, and every opening is logged below. You can also ask the assistant what one of them says." />
      {error && <ErrorNote message={error.message} />}
      {data && data.length === 0 && (
        <div className="panel"><Empty title="Nothing filed for you yet">HR files your contract, amendments and certificates here.</Empty></div>
      )}
      {!!data?.length && (
        <ul className="panel divide-y divide-rule">
          {data.map((f) => (
            <li key={f.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
              <div className="flex items-start gap-3">
                <FileText size={20} className="mt-0.5 shrink-0 text-ink-soft" aria-hidden />
                <div>
                  <p className="font-medium">{f.title}</p>
                  <p className="text-sm text-ink-soft">{f.kind_label} · filed {day(f.filed_on)} {f.filed_on.slice(0, 4)}{f.filed_by ? ` by ${f.filed_by}` : ""}</p>
                  <p className="text-sm text-ink-soft">{viewsLine(f)}</p>
                  {f.assistant === "unreadable" && <p className="text-sm text-wait">The assistant can't read this one yet.</p>}
                </div>
              </div>
              <a className="btn-quiet shrink-0" href={`/api/files/${f.id}/file`} target="_blank" rel="noreferrer">Open</a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
