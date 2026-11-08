"use client";
// The new hire's page, opened from the link in their welcome email. Outside the signed-in app: no menu, no
// other pages. The 6-digit code HR gave them is checked on the server; the checklist and the assistant are
// the only things this session can reach. Works on a phone: one column, checklist first.
import { FormEvent, useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, ApiError, fetcher } from "@/lib/api";
import { day, dayWithWeekday, relative } from "@/lib/format";
import { WelcomeStep, WelcomeView } from "@/lib/types";
import { ErrorNote } from "@/components/PageHeader";
import { WelcomeAssistant } from "@/components/WelcomeAssistant";

const STATE_NOTE: Record<string, string> = {
  expired: "This link has expired. Ask HR for a new one.",
  locked: "Too many wrong codes, so this link is locked. Ask HR for a new link.",
  revoked: "This link was replaced by a newer one. Use the latest link HR sent you, or ask HR.",
  invalid: "This link isn't valid. Check you opened the newest link, or ask HR.",
};

export default function Welcome({ params }: { params: { token: string } }) {
  const flag = `welcome:${params.token}`;                 // this tab already entered the code for THIS link
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const { data: link } = useSWR<{ state: string }>(`/welcome/${params.token}`, fetcher, { revalidateOnFocus: false });
  const { data: view, error } = useSWR<WelcomeView>(signedIn ? "/welcome-me" : null, fetcher, { revalidateOnFocus: true });

  useEffect(() => { try { setSignedIn(sessionStorage.getItem(flag) === "1"); } catch { setSignedIn(false); } }, [flag]);
  useEffect(() => { if (error instanceof ApiError && error.status === 401) setSignedIn(false); }, [error]);

  async function signOut() {
    try { sessionStorage.removeItem(flag); } catch {}
    await api("/welcome-me", { method: "DELETE" }).catch(() => {});
    await mutate(() => true, undefined, { revalidate: false });
    setSignedIn(false);
  }

  return (
    <main className="min-h-screen bg-paper">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-[1080px] items-center justify-between px-4 py-3">
          <span className="flex items-center gap-2">
            <span aria-hidden className="grid h-8 w-8 place-items-center rounded bg-dossier font-semibold text-white">C</span>
            <span className="text-md font-semibold">CognivexAI</span>
          </span>
          {signedIn && view && <button className="btn-ghost" onClick={signOut}>Sign out</button>}
        </div>
      </header>
      <div className="mx-auto max-w-[1080px] px-4 py-6 sm:py-10">
        {signedIn === null || (signedIn && !view && !error) ? <p className="text-ink-soft">Loading…</p>
          : signedIn && view ? <Home view={view} />
          : <CodeForm token={params.token} state={link?.state} onDone={() => { try { sessionStorage.setItem(flag, "1"); } catch {} setSignedIn(true); }} />}
      </div>
    </main>
  );
}

function CodeForm({ token, state, onDone }: { token: string; state?: string; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const blocked = state && state !== "ready" ? STATE_NOTE[state] : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api("/welcome/login", { body: { token, code } });
      onDone();
    } catch (x) { setErr((x as Error).message); setCode(""); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-[440px] pt-4 sm:pt-12">
      <h1 className="text-xl font-semibold tracking-[-0.01em]">Welcome to CognivexAI</h1>
      {blocked ? <div className="mt-5"><ErrorNote message={blocked} /></div> : (
        <form onSubmit={submit} className="mt-3 space-y-4">
          <p className="text-ink-soft">Enter the 6-digit code HR gave you, by phone or message. It isn't in the email.</p>
          <label className="block">
            <span className="label">Your code</span>
            <input className="field h-12 text-center text-xl tracking-[0.4em]" inputMode="numeric" autoComplete="one-time-code"
                   maxLength={6} pattern="[0-9]{6}" required autoFocus value={code}
                   onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} aria-label="6-digit code" />
          </label>
          {err && <ErrorNote message={err} />}
          <button className="btn-primary h-11 w-full" disabled={busy || code.length !== 6}>Open my page</button>
        </form>
      )}
    </div>
  );
}

function Home({ view }: { view: WelcomeView }) {
  const first = view.name.split(" ")[0];
  return (
    <>
      <h1 className="text-xl font-semibold tracking-[-0.01em]">Welcome, {first}</h1>
      <p className="mt-1 text-ink-soft">
        Your first day is {dayWithWeekday(view.start_date)} ({relative(view.start_date)}) as {view.job_title}
        {view.manager ? `, in ${view.manager}'s team` : ""}.
      </p>
      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)] lg:items-start">
        <Checklist view={view} />
        <div className="lg:sticky lg:top-6"><WelcomeAssistant /></div>
      </div>
    </>
  );
}

function Checklist({ view }: { view: WelcomeView }) {
  const next = view.steps.find((s) => s.who === "you" && s.status === "todo");
  return (
    <section aria-labelledby="list" className="panel">
      <div className="px-5 pt-5">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="list" className="font-medium">Your checklist</h2>
          <p className="text-sm text-ink-soft">{view.done} of {view.total} done</p>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-rule" role="progressbar" aria-valuemin={0} aria-valuemax={view.total}
             aria-valuenow={view.done} aria-label="Checklist progress">
          <div className="h-full bg-dossier" style={{ width: `${(view.done / view.total) * 100}%` }} />
        </div>
      </div>
      <div className="mx-5 mt-4 rounded border border-rule bg-paper px-4 py-3">
        {next ? (
          <>
            <p className="text-sm text-ink-soft">Your next step</p>
            <p className="font-medium">{next.step}</p>
            <p className="text-sm text-ink-soft">{next.what} Due {day(next.due_date)}.</p>
          </>
        ) : (
          <p>Nothing is waiting on you right now. HR and your manager are preparing the rest.</p>
        )}
      </div>
      <ol className="mt-4 divide-y divide-rule border-t border-rule">
        {view.steps.map((s) => <Row key={s.step} s={s} />)}
      </ol>
    </section>
  );
}

function Row({ s }: { s: WelcomeStep }) {
  const [color, label] = s.status === "done" ? ["bg-ok", "Done"] : s.status === "skipped" ? ["bg-off", "Not needed"]
    : s.overdue ? ["bg-no", "Overdue"] : ["bg-wait", "To do"];
  return (
    <li className="px-5 py-3">
      <p className={s.status === "done" ? "font-medium text-ink-soft" : "font-medium"}>{s.step}</p>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-ink-soft">
        <span className="inline-flex items-center gap-1.5 text-ink"><span className={`h-2 w-2 rounded-full ${color}`} aria-hidden />{label}</span>
        <span>{s.who === "you" ? "For you" : s.who === "HR" ? "HR takes care of it" : "Your manager takes care of it"}</span>
        {s.status === "todo" && <span>Due {day(s.due_date)}</span>}
      </p>
    </li>
  );
}
