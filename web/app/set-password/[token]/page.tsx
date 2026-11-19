"use client";
// Opened from the link HR sends (a first password, or a forgotten one). Outside the signed-in app. The server decides who the link
// is for; the page never says. Setting the password signs the person in and signs them out everywhere else.
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { ErrorNote } from "@/components/PageHeader";

const STATE_NOTE: Record<string, string> = {
  used: "This link was already used. Sign in with your password, or ask HR for a new link.",
  replaced: "This link was replaced by a newer one. Use the latest link HR sent you.",
  expired: "This link has expired. Ask HR for a new one.",
  invalid: "This link isn't valid. Check you opened the newest link, or ask HR.",
};
const MIN_LENGTH = 12;     // the server checks it too (accounts.py)

export default function SetPassword({ params }: { params: { token: string } }) {
  const router = useRouter();
  const { data: link, error } = useSWR<{ state: string }>(`/set-password/${params.token}`, fetcher, { revalidateOnFocus: false });
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const blocked = link && link.state !== "ready" ? STATE_NOTE[link.state] : null;
  const mismatch = again.length > 0 && again !== password;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api(`/set-password/${params.token}`, { body: { password } });
      await mutate(() => true, undefined, { revalidate: false });
      router.replace("/");
    } catch (x) {
      setErr((x as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-paper px-4 py-12 sm:py-20">
      <div className="mx-auto max-w-[440px]">
        <div className="flex items-center gap-2">
          <span aria-hidden className="grid h-8 w-8 place-items-center rounded bg-dossier text-white font-semibold">C</span>
          <span className="text-md font-semibold">CognivexAI</span>
        </div>
        <h1 className="mt-10 text-xl font-semibold tracking-[-0.01em]">Set your password</h1>
        {error ? <div className="mt-5"><ErrorNote message={error.message} /></div>
          : !link ? <p className="mt-5 text-ink-soft">Loading…</p>
          : blocked ? <div className="mt-5"><ErrorNote message={blocked} /></div>
          : (
            <form onSubmit={submit} className="mt-3 space-y-4">
              <p className="text-ink-soft">At least {MIN_LENGTH} characters. A few words you will remember work well. This link works once.</p>
              <label className="block">
                <span className="label">New password</span>
                <input className="field" type="password" autoComplete="new-password" required autoFocus minLength={MIN_LENGTH}
                       value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
              <label className="block">
                <span className="label">The same password again</span>
                <input className="field" type="password" autoComplete="new-password" required value={again}
                       onChange={(e) => setAgain(e.target.value)} aria-invalid={mismatch} />
              </label>
              {mismatch && <p className="text-sm text-no">The two passwords are different.</p>}
              {err && <ErrorNote message={err} />}
              <button className="btn-primary h-11 w-full" disabled={busy || password.length < MIN_LENGTH || again !== password}>
                {busy ? "Saving…" : "Set password and sign in"}
              </button>
            </form>
          )}
      </div>
    </main>
  );
}
