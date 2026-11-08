"use client";
// HR's policy editor on the Handbook page: edit, add or remove a section (with a preview), and the history of versions (read one, restore one).
// The rules (only HR, one version per save, the sentences the app relies on, the stale-version check, the re-index for the assistant)
// are in policy_store.py.
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { PolicySaved, PolicyVersion } from "@/lib/types";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";

export type Draft = { original: string | null; title: string; text: string };

export function SectionPreview({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded border border-rule bg-paper px-4 py-4">
      <h3 className="text-md font-semibold">{title.trim() || "(no title)"}</h3>
      <p className="mt-2 whitespace-pre-line leading-relaxed">{text.trim() || "(no text)"}</p>
    </div>
  );
}

export function EditDialog({ draft, baseVersion, kept, onSaved, onClose }:
  { draft: Draft; baseVersion: number; kept: string[]; onSaved: (r: PolicySaved) => void; onClose: () => void }) {
  const [title, setTitle] = useState(draft.title);
  const [text, setText] = useState(draft.text);
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const adding = draft.original === null;

  async function send(path: string, body: object) {
    setBusy(true);
    setError("");
    try {
      const result = await api<PolicySaved>(path, { body });
      await Promise.all([mutate("/handbook"), mutate("/hr/policy"), mutate("/hr/policy/versions")]);
      onSaved(result);
      onClose();
    } catch (x) { setError((x as Error).message); setBusy(false); }
  }
  const save = (e: FormEvent) => {
    e.preventDefault();
    send("/hr/policy/section", { base_version: baseVersion, original_title: draft.original ?? "", title, text, note });
  };

  return (
    <Dialog open onClose={onClose} title={adding ? "Add a section" : `Edit "${draft.original}"`} width="max-w-2xl">
      <form onSubmit={save} className="space-y-4">
        <div className="flex gap-1" role="tablist" aria-label="Edit or preview">
          <button type="button" role="tab" aria-selected={!preview} className={preview ? "btn-ghost" : "btn-quiet"} onClick={() => setPreview(false)}>Edit</button>
          <button type="button" role="tab" aria-selected={preview} className={preview ? "btn-quiet" : "btn-ghost"} onClick={() => setPreview(true)}>Preview</button>
        </div>
        {preview ? (
          <div className="space-y-2">
            <SectionPreview title={title} text={text} />
            <p className="text-sm text-ink-soft">This is what everyone reads in the Handbook, and what the assistant answers from.</p>
          </div>
        ) : (
          <>
            <label className="block"><span className="label">Title</span>
              <input className="field" required maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="For example: Company car" /></label>
            <label className="block"><span className="label">Text</span>
              <textarea className="field h-auto py-2 leading-relaxed" required rows={9} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)}
                        placeholder="Write the rule the way you want employees to read it." /></label>
            <label className="block"><span className="label">What changed, and why (optional)</span>
              <input className="field" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: new rule from the 2025 agreement" /></label>
          </>
        )}
        <details className="text-sm text-ink-soft">
          <summary className="cursor-pointer">Sentences the app relies on</summary>
          <p className="mt-1">The leave rules in the app use these, so a save that takes one out is refused. To change the rule itself, ask a developer.</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{kept.map((k) => <li key={k}>{k}</li>)}</ul>
        </details>
        {error && <ErrorNote message={error} />}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            {!adding && !confirmDelete && <button type="button" className="btn-ghost" onClick={() => setConfirmDelete(true)}>Delete this section</button>}
            {!adding && confirmDelete && (
              <span className="flex items-center gap-2">
                <button type="button" className="btn-danger" disabled={busy}
                        onClick={() => send("/hr/policy/section/delete", { base_version: baseVersion, title: draft.original, note })}>Delete it</button>
                <button type="button" className="btn-ghost" onClick={() => setConfirmDelete(false)}>Keep it</button>
              </span>
            )}
          </div>
          <div className="flex gap-2">
            <button type="button" className="btn-quiet" onClick={onClose}>Cancel</button>
            <button className="btn-primary" disabled={busy || !title.trim() || !text.trim()}>{busy ? "Saving… the assistant is learning it" : "Save"}</button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}

export function HistoryDialog({ baseVersion, onSaved, onClose }: { baseVersion: number; onSaved: (r: PolicySaved) => void; onClose: () => void }) {
  const { data } = useSWR<PolicyVersion[]>("/hr/policy/versions", fetcher);
  const [open, setOpen] = useState<number | null>(null);
  const [sections, setSections] = useState<{ title: string; text: string }[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function read(id: number) {
    if (open === id) { setOpen(null); return; }
    setOpen(id);
    setSections(null);
    try { setSections(await api(`/hr/policy/versions/${id}`)); } catch (x) { setError((x as Error).message); }
  }
  async function restore(id: number) {
    setBusy(true);
    setError("");
    try {
      const result = await api<PolicySaved>("/hr/policy/restore", { body: { base_version: baseVersion, version_id: id } });
      await Promise.all([mutate("/handbook"), mutate("/hr/policy"), mutate("/hr/policy/versions")]);
      onSaved(result);
      onClose();
    } catch (x) { setError((x as Error).message); setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title="Policy history" width="max-w-2xl">
      <p className="mb-3 text-ink-soft">Every save is a version, and nothing is overwritten. Restoring an old one makes it the current policy again, as a new version.</p>
      {!data ? <p className="text-ink-soft">Loading…</p> : (
        <ul className="max-h-[60vh] divide-y divide-rule overflow-y-auto rounded border border-rule">
          {data.map((v) => (
            <li key={v.id} className="px-4 py-3">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="font-medium break-words">{v.id === 0 ? "The policy as shipped" : `Version ${v.id}`} {v.current && <span className="ml-1 rounded bg-paper px-1.5 py-0.5 text-xs font-normal text-ink-soft">current</span>}</p>
                  {v.id !== 0 && <p className="text-sm break-words">{v.change}</p>}
                  <p className="text-sm text-ink-soft">{v.saved_by ? `${v.saved_by}, ${timestamp(v.saved_at ?? "")}` : "The file the app came with"}</p>
                  {v.note && <p className="text-sm text-ink-soft break-words">“{v.note}”</p>}
                </div>
                <div className="flex shrink-0 gap-2">
                  <button className="btn-ghost" onClick={() => read(v.id)}>{open === v.id ? "Hide" : "Read"}</button>
                  {!v.current && <button className="btn-quiet" disabled={busy} onClick={() => restore(v.id)}>Restore</button>}
                </div>
              </div>
              {open === v.id && (
                <div className="mt-3 space-y-3 rounded bg-paper px-4 py-3 text-sm">
                  {!sections ? "Loading…" : sections.map((s) => (
                    <div key={s.title}><p className="font-medium">{s.title}</p><p className="whitespace-pre-line text-ink-soft">{s.text}</p></div>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}
      <div className="mt-4 flex justify-end"><button className="btn-quiet" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}
