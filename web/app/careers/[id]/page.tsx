"use client";
// One open job, the privacy notice, and the form to apply. No login. The CV is sent with the form as base64 in the request body (never in the
// address), checked on the server before anything is saved. When it is saved the person gets a private tracking link, shown once: nothing is
// emailed. Two boxes, kept apart on purpose: the first (required) is for handling this application, the second (optional) is the talent pool.
import Link from "next/link";
import { FormEvent, useState } from "react";
import useSWR from "swr";
import { ArrowLeft } from "lucide-react";
import { api, ApiError, fetcher } from "@/lib/api";
import { Notice, PublicJob } from "@/lib/types";
import { CareersFrame } from "@/components/CareersFrame";
import { salaryText } from "@/components/JobParts";
import { ErrorNote } from "@/components/PageHeader";

const MAX_CV = 5 * 1024 * 1024;

async function toBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export default function CareersJob({ params }: { params: { id: string } }) {
  const { data, error } = useSWR<{ job: PublicJob; notice: Notice; retention_months: number }>(`/careers/jobs/${params.id}`, fetcher);
  const [link, setLink] = useState("");

  return (
    <CareersFrame>
      <Link href="/careers" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-soft hover:text-ink"><ArrowLeft size={14} aria-hidden /> All jobs</Link>
      {error instanceof ApiError && error.status === 404 && (
        <div className="panel px-5 py-10 text-center"><p className="font-medium">This job isn't open</p><p className="mt-1 text-ink-soft">It may have been filled. <Link href="/careers" className="text-dossier hover:underline">See the open jobs</Link>.</p></div>
      )}
      {error && !(error instanceof ApiError && error.status === 404) && <ErrorNote message={error.message} />}
      {!data && !error && <p className="text-ink-soft">Loading…</p>}
      {data && (
        <>
          <h1 className="text-xl font-semibold tracking-[-0.01em]">{data.job.title}</h1>
          <p className="mt-1 text-ink-soft">{data.job.team}, {data.job.location}, {data.job.contract_type}{salaryText(data.job) ? `. ${salaryText(data.job)}` : ""}</p>
          <section aria-labelledby="about" className="panel mt-5 px-5 py-4">
            <h2 id="about" className="sr-only">About the job</h2>
            <p className="whitespace-pre-line">{data.job.description}</p>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div><h3 className="text-sm font-medium text-ink-soft">What we need</h3><ul className="mt-1 list-disc space-y-0.5 pl-5">{data.job.must_have.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
              {data.job.nice_to_have.length > 0 && <div><h3 className="text-sm font-medium text-ink-soft">Nice to have</h3><ul className="mt-1 list-disc space-y-0.5 pl-5">{data.job.nice_to_have.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
            </div>
          </section>

          <section aria-labelledby="privacy" className="panel mt-6 px-5 py-4">
            <h2 id="privacy" className="font-medium">Privacy notice</h2>
            <dl className="mt-2 space-y-3 text-sm">
              {data.notice.sections.map((s) => <div key={s.title}><dt className="font-medium">{s.title}</dt><dd className="text-ink-soft">{s.text}</dd></div>)}
            </dl>
          </section>

          {link ? <Done link={link} /> : <ApplyForm jobId={data.job.id} months={data.retention_months} onDone={setLink} />}
        </>
      )}
    </CareersFrame>
  );
}

function ApplyForm({ jobId, months, onDone }: { jobId: number; months: number; onDone: (link: string) => void }) {
  const [f, setF] = useState({ name: "", email: "", phone: "", cover_letter: "", consent: false, talent_pool: false });
  const [cv, setCv] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!cv) return setError("Attach your CV as a PDF file.");
    if (cv.size > MAX_CV) return setError("That file is too big. The limit is 5 MB.");
    setBusy(true);
    try {
      const result = await api<{ link: string }>(`/careers/jobs/${jobId}/apply`, { body: { ...f, cv: await toBase64(cv) } });
      onDone(result.link);
    } catch (x) { setError((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={send} className="panel mt-6 space-y-4 px-5 py-5" aria-labelledby="apply">
      <h2 id="apply" className="font-medium">Apply</h2>
      <label className="block"><span className="label">Name</span><input className="field" required autoComplete="name" maxLength={120} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="block"><span className="label">Email</span><input className="field" type="email" required autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
      <label className="block"><span className="label">Phone (optional)</span><input className="field" type="tel" autoComplete="tel" maxLength={40} value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></label>
      <label className="block"><span className="label">CV (PDF, up to 5 MB)</span>
        <input className="block w-full text-base" type="file" accept="application/pdf,.pdf" required onChange={(e) => setCv(e.target.files?.[0] ?? null)} />
        <span className="mt-1 block text-sm text-ink-soft">Please leave out your photo, age, marital status and nationality: we don't use them.</span>
      </label>
      <label className="block"><span className="label">Cover letter (optional)</span>
        <textarea className="field h-auto py-2 leading-relaxed" rows={5} maxLength={3000} value={f.cover_letter} onChange={(e) => setF({ ...f, cover_letter: e.target.value })} /></label>
      <label className="flex items-start gap-2.5">
        <input type="checkbox" required className="mt-1 h-4 w-4 shrink-0 accent-dossier" checked={f.consent} onChange={(e) => setF({ ...f, consent: e.target.checked })} />
        <span>I have read the privacy notice above and agree that CognivexAI handles my application as it describes.</span>
      </label>
      <label className="flex items-start gap-2.5">
        <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-dossier" checked={f.talent_pool} onChange={(e) => setF({ ...f, talent_pool: e.target.checked })} />
        <span>Optional: keep my application in your talent pool for up to {months} months after our last contact, so you can get in touch about other jobs. I can leave it at any time.</span>
      </label>
      {error && <ErrorNote message={error} />}
      <button className="btn-primary" disabled={busy}>{busy ? "Sending…" : "Send my application"}</button>
    </form>
  );
}

function Done({ link }: { link: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="panel mt-6 space-y-3 px-5 py-5" aria-live="polite">
      <h2 className="font-medium">Thank you, your application is sent</h2>
      <p>A person at CognivexAI will read it. This private link shows where it stands. <strong>Save it now: we don't email it</strong>, and it's the only way to see your progress.</p>
      <p className="break-all rounded border border-rule bg-paper px-3 py-2 text-sm">{link}</p>
      <div className="flex flex-wrap gap-2">
        <button className="btn-quiet" onClick={async () => { try { await navigator.clipboard.writeText(link); setCopied(true); } catch {} }}>{copied ? "Copied" : "Copy the link"}</button>
        <a className="btn-primary" href={link}>Open my page</a>
      </div>
    </section>
  );
}
