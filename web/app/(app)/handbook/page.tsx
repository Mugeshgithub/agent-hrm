"use client";
// The company's HR policy, and the collective agreement. Everyone reads the policy here; HR also edits it (the rules are in policy_store.py).
// Opened from a Needs-you question ("/handbook?gap=3"), it says which question the person is answering, and offers to mark it fixed once they have saved.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { PolicyForEditing, PolicyGap, PolicySaved } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { CollectiveAgreementCard } from "@/components/CollectiveAgreementCard";
import { Draft, EditDialog, HistoryDialog } from "@/components/PolicyEditor";
import { useMe } from "@/components/Me";

type Section = { title: string; text: string };
const slug = (s: string) => s.toLowerCase().normalize("NFD").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");

export default function Handbook() {
  // useSearchParams needs a Suspense boundary above it for the page to be built
  return <Suspense fallback={null}><HandbookPage /></Suspense>;
}

function HandbookPage() {
  const me = useMe();
  const isHR = me.role === "hr";
  const router = useRouter();
  const gapId = Number(useSearchParams().get("gap")) || null;
  const { data } = useSWR<Section[]>("/handbook", fetcher);
  const { data: policy } = useSWR<PolicyForEditing>(isHR ? "/hr/policy" : null, fetcher);
  const { data: gaps } = useSWR<PolicyGap[]>(isHR && gapId ? "/hr/policy-gaps" : null, fetcher);
  const gap = gaps?.find((g) => g.id === gapId);
  const [editing, setEditing] = useState<Draft | null>(null);
  const [history, setHistory] = useState(false);
  const [saved, setSaved] = useState<PolicySaved | null>(null);      // the last save in this visit: it is what "mark fixed" points at
  const [error, setError] = useState("");

  async function markFixed() {
    if (!gap || !saved) return;
    setError("");
    try {
      await api(`/hr/policy-gaps/${gap.id}/fixed`, { body: { note: `Policy updated, version ${saved.version}: ${saved.change}` } });
      mutate(() => true);
      router.push("/needs-you");
    } catch (e) { setError((e as Error).message); }
  }

  return (
    <>
      <PageHeader title="Handbook"
        description={isHR ? "The company's HR policy. The assistant answers from this text, and from the Code du travail for legal questions. You can edit it: every save is a version."
                          : "The company's HR policy. The assistant answers from this text, and from the Code du travail for legal questions."} />
      <CollectiveAgreementCard />

      {isHR && gapId && (
        <section aria-label="The question you are answering" className="panel mb-6 border-l-4 border-l-dossier px-5 py-4">
          {gaps && !gap ? (
            <p>That question is no longer open. <Link href="/needs-you" className="text-dossier hover:underline">Back to Needs you</Link></p>
          ) : gap && (
            <>
              <p className="font-medium break-words">You're answering a question the policy doesn't answer: “{gap.topic}”</p>
              <p className="text-sm text-ink-soft">
                Asked {gap.asks === 1 ? "once" : `${gap.asks} times`}. Edit a section or add one, and save. Then mark the question fixed.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {policy && <button className="btn-quiet" onClick={() => setEditing({ original: null, title: gap.topic.charAt(0).toUpperCase() + gap.topic.slice(1), text: "" })}>Add a section about this</button>}
                {saved ? <button className="btn-primary" onClick={markFixed}>Mark fixed (saved as version {saved.version})</button>
                       : <span className="text-sm text-ink-soft">"Mark fixed" appears here once you have saved.</span>}
                <Link href="/needs-you" className="btn-ghost">Back to Needs you</Link>
              </div>
              {error && <p role="alert" className="mt-2 text-sm text-no">{error}</p>}
            </>
          )}
        </section>
      )}

      {isHR && policy && (
        <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-ink-soft">
            {policy.original ? "The policy as shipped: nobody has edited it yet." : `Version ${policy.version}, saved by ${policy.saved_by ?? "HR"}, ${timestamp(policy.saved_at ?? "")}: ${policy.change}.`}
            {saved && !saved.indexed && " The assistant's search will learn your last change on its next question."}
          </p>
          <div className="flex shrink-0 gap-2">
            <button className="btn-ghost" onClick={() => setHistory(true)}>History</button>
            <button className="btn-quiet" onClick={() => setEditing({ original: null, title: "", text: "" })}>Add a section</button>
          </div>
        </div>
      )}

      <div className="grid gap-8 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Sections" className="hidden lg:block">
          <ul className="sticky top-20 space-y-1 text-sm">
            {data?.map((s) => <li key={s.title}><a href={`#${slug(s.title)}`} className="block py-1 text-ink-soft hover:text-dossier">{s.title}</a></li>)}
          </ul>
        </nav>
        <article className="panel px-6 py-6 sm:px-8 space-y-8 max-w-[760px]">
          {data?.map((s) => (
            <section key={s.title} id={slug(s.title)} className="scroll-mt-20">
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-md font-semibold">{s.title}</h2>
                {isHR && policy && <button className="btn-ghost -mt-1 h-8 shrink-0 px-2" onClick={() => setEditing({ original: s.title, title: s.title, text: s.text })}
                                           aria-label={`Edit ${s.title}`}>Edit</button>}
              </div>
              <p className="mt-2 whitespace-pre-line leading-relaxed max-w-[68ch]">{s.text}</p>
            </section>
          ))}
        </article>
      </div>

      {editing && policy && <EditDialog draft={editing} baseVersion={policy.version} kept={policy.kept} onSaved={setSaved} onClose={() => setEditing(null)} />}
      {history && policy && <HistoryDialog baseVersion={policy.version} onSaved={setSaved} onClose={() => setHistory(false)} />}
    </>
  );
}
