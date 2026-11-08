"use client";
// A small dialog that asks for a reason or a note and posts it. The note goes in the change history with the
// person's name, so it is always required.
import { FormEvent, useState } from "react";
import { mutate } from "swr";
import { api } from "@/lib/api";
import { Dialog } from "@/components/Dialog";
import { ErrorNote } from "@/components/PageHeader";

export function TextDialog({ title, intro, label = "Reason", placeholder, endpoint, field, submit, danger, onClose }:
  { title: string; intro: string; label?: string; placeholder?: string; endpoint: string; field: "reason" | "note";
    submit: string; danger?: boolean; onClose: () => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(endpoint, { body: { [field]: text } });
      mutate(() => true);
      onClose();
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onClose={onClose} title={title}>
      <form onSubmit={send} className="space-y-4">
        <p className="text-ink-soft">{intro}</p>
        <label className="block">
          <span className="label">{label}</span>
          <input className="field" required value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} />
          <span className="mt-1 block text-sm text-ink-soft">Saved in the change history with your name.</span>
        </label>
        {error && <ErrorNote message={error} />}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={onClose}>Not now</button>
          <button className={danger ? "btn-danger" : "btn-primary"} disabled={busy || !text.trim()}>{submit}</button>
        </div>
      </form>
    </Dialog>
  );
}
