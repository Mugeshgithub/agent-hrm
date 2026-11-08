"use client";
// The company's collective agreement (convention collective), on the Handbook page. Everyone who works here can open it;
// only HR adds, replaces or removes it. The rules (who may, the file checks, the indexing for the assistant, the order the
// assistant uses its sources in) are in collective_agreement.py.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { CollectiveAgreement } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { useMe } from "@/components/Me";
import { ErrorNote } from "@/components/PageHeader";

export function CollectiveAgreementCard() {
  const me = useMe();
  const isHR = me.role === "hr";
  const { data, isLoading } = useSWR<CollectiveAgreement | null>("/collective-agreement", fetcher);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState(false);
  if (isLoading) return null;
  if (!data && !isHR) return null;       // nothing to show an employee until HR adds one
  return (
    <section aria-labelledby="agreement" className="panel mb-6 px-5 py-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <h2 id="agreement" className="font-medium">Collective agreement</h2>
          {data ? (
            <>
              <p className="mt-1 break-words font-medium">{data.name}</p>
              <p className="text-sm text-ink-soft">
                Added by {data.uploaded_by ?? "HR"}, {timestamp(data.uploaded_at)} · {data.sections} {data.sections === 1 ? "article" : "articles"}
                {" · "}{data.media_type === "application/pdf" ? "PDF" : "text"}, {Math.max(1, Math.round(data.size_bytes / 1024))} KB
              </p>
              {data.assistant === "partial" && <p className="text-sm text-wait">The file is long and was scanned, so the assistant reads only the start of it.</p>}
              <p className="mt-2 text-sm text-ink-soft max-w-[68ch]">
                The assistant answers from your contract first, then this agreement, then the company policy (below), then the law.
              </p>
            </>
          ) : (
            <p className="mt-1 text-ink-soft max-w-[68ch]">
              Not added yet. Add the company's convention collective (a PDF or a text file) and the assistant will answer from it, after the person's contract and before the company policy.
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {data && <a className="btn-quiet" href="/api/collective-agreement/file" target="_blank" rel="noreferrer">Open</a>}
          {isHR && <button className="btn-quiet" onClick={() => setAdding(true)}>{data ? "Replace" : "Add the agreement"}</button>}
          {isHR && data && <button className="btn-ghost" onClick={() => setRemoving(true)}>Remove</button>}
        </div>
      </div>
      {adding && <AddDialog current={data ?? null} onClose={() => setAdding(false)} />}
      {removing && data && <RemoveDialog name={data.name} onClose={() => setRemoving(false)} />}
    </section>
  );
}

function AddDialog({ current, onClose }: { current: CollectiveAgreement | null; onClose: () => void }) {
  const [name, setName] = useState(current?.name ?? "");
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
      const res = await fetch(`/api/hr/collective-agreement?name=${encodeURIComponent(name.trim())}&filename=${encodeURIComponent(file.name)}`,
        { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work. Try again.");
      await mutate("/collective-agreement");
      if (data.assistant === "ready") onClose();
      else setNote("Added. The file is a long scan, so the assistant will only read the start of it.");
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title={current ? "Replace the collective agreement" : "Add the collective agreement"}>
      {note ? (
        <div className="space-y-4"><p>{note}</p><div className="flex justify-end"><button className="btn-primary" onClick={onClose}>Done</button></div></div>
      ) : (
        <form onSubmit={save} className="space-y-4">
          <p className="text-ink-soft">
            Everyone who works here will be able to open it, and the assistant will answer from it. A PDF or a text file up to 10 MB.
            {current && " The current one is replaced and its file deleted."}
          </p>
          <label className="block"><span className="label">Name</span>
            <input className="field" required value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Convention collective Syntec" /></label>
          <label className="block"><span className="label">File</span>
            <input className="field h-auto py-2" type="file" required accept="application/pdf,text/plain,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          {error && <ErrorNote message={error} />}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={busy || !file || !name.trim()}>{busy ? "Adding… the assistant is reading it" : current ? "Replace it" : "Add it"}</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function RemoveDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function remove() {
    setBusy(true);
    setError("");
    try {
      await api("/hr/collective-agreement/remove", { body: {} });
      await mutate("/collective-agreement");
      onClose();
    } catch (x) { setError((x as Error).message); setBusy(false); }
  }
  return (
    <Dialog open onClose={onClose} title="Remove the collective agreement">
      <div className="space-y-4">
        <p>The file of "{name}" is deleted, and the assistant stops answering from it.</p>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button className="btn-quiet" onClick={onClose}>Keep it</button>
          <button className="btn-danger" disabled={busy} onClick={remove}>Remove it</button>
        </div>
      </div>
    </Dialog>
  );
}
