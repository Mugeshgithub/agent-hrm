"use client";
// HR's documents card on a person's page: file a contract, an amendment or a certificate, open what is there (every
// opening is logged), or delete one with a reason. The rules (only HR, the file checks, the private storage, and
// the transcription for the assistant) are in employee_files.py.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { fetcher } from "@/lib/api";
import { day, timestamp } from "@/lib/format";
import { PersonFile } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";
import { TextDialog } from "@/components/TextDialog";

const KINDS: Record<string, string> = { contract: "Contract", amendment: "Amendment", certificate: "Certificate" };

export function PersonFiles({ empId, firstName }: { empId: string; firstName: string }) {
  const { data } = useSWR<PersonFile[]>(`/hr/people/${empId}/files`, fetcher);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<PersonFile | null>(null);
  return (
    <section aria-labelledby="files" className="panel">
      <div className="flex items-start justify-between gap-4 px-5 pt-5">
        <div>
          <h2 id="files" className="font-medium">Documents</h2>
          <p className="text-sm text-ink-soft">Private to {firstName} and HR. Every opening is logged.</p>
        </div>
        <button className="btn-quiet shrink-0" onClick={() => setAdding(true)}>Add a document</button>
      </div>
      {!data ? <p className="px-5 py-5 text-ink-soft">Loading…</p> : data.length === 0 ? (
        <p className="px-5 py-5 text-ink-soft">Nothing filed yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-rule border-t border-rule">
          {data.map((f) => (
            <li key={f.id} className="flex flex-col gap-2 px-5 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
              <div className="min-w-0">
                <p className="font-medium">{f.title}</p>
                <p className="text-sm text-ink-soft">{f.kind_label} · filed {day(f.filed_on)} {f.filed_on.slice(0, 4)}{f.filed_by ? ` by ${f.filed_by}` : ""}</p>
                <p className="text-sm text-ink-soft">
                  {f.views.length ? `Last opened by ${f.views[0].viewer_name ?? "someone"}${f.views[0].via === "assistant" ? " (the assistant)" : ""}, ${timestamp(f.views[0].viewed_at)}` : "Not opened yet"}
                  {f.assistant === "unreadable" ? " · the assistant can't read it" : f.assistant === "partial" ? " · the assistant reads only the start" : ""}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <a className="btn-quiet" href={`/api/files/${f.id}/file`} target="_blank" rel="noreferrer">Open</a>
                <button className="btn-ghost" onClick={() => setDeleting(f)}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {adding && <AddDialog empId={empId} firstName={firstName} onClose={() => setAdding(false)} />}
      {deleting && (
        <TextDialog title={`Delete "${deleting.title}"`} danger submit="Delete it" field="reason" endpoint={`/hr/files/${deleting.id}/delete`}
                    intro="The file and its text are deleted for good. The change history keeps who deleted it and why."
                    placeholder="For example: filed under the wrong person" onClose={() => setDeleting(null)} />
      )}
    </section>
  );
}

function AddDialog({ empId, firstName, onClose }: { empId: string; firstName: string; onClose: () => void }) {
  const [kind, setKind] = useState("contract");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/hr/people/${empId}/files?kind=${kind}&title=${encodeURIComponent(title)}&name=${encodeURIComponent(file.name)}`,
        { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work. Try again.");
      await Promise.all([mutate(`/hr/people/${empId}/files`), mutate(`/people/${empId}/history`)]);
      if (data.assistant === "ready") onClose();
      else setNote(data.assistant === "partial" ? "Added. The file is long, so the assistant will only read the start of it."
                                                 : "Added. The assistant couldn't read the text of this file, but the file itself is there.");
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title={`Add a document for ${firstName}`}>
      {note ? (
        <div className="space-y-4"><p>{note}</p><div className="flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div></div>
      ) : (
        <form onSubmit={save} className="space-y-4">
          <p className="text-ink-soft">Only {firstName} and HR will be able to open it. A PDF, JPG or PNG up to 10 MB.</p>
          <label className="block"><span className="label">What is it?</span>
            <select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></label>
          <label className="block"><span className="label">Title</span>
            <input className="field" required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="For example: Employment contract, 2024" /></label>
          <label className="block"><span className="label">File</span>
            <input className="field h-auto py-2" type="file" required accept="application/pdf,image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          {error && <ErrorNote message={error} />}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={busy || !file || !title.trim()}>{busy ? "Adding… the assistant is reading it" : "Add the document"}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
