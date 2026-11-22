"use client";
// Sign in with your work email and the password you set from HR's link. The demo picker (sign in as anyone, no password)
// shows only when the API has it switched on (HRM_DEMO_LOGIN=1): never on a real deployment.
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { Avatar } from "@/components/Shell";
import { ErrorNote } from "@/components/PageHeader";

type DemoUser = { id: string; name: string; role: string; title: string; department: string };

// the six people the demo story is built around; everyone else is found by search
const FEATURED = ["E001", "M001", "H001", "C001", "E002", "E003"];

const ROLE_NOTE: Record<string, string> = {
  employee: "Requests time off, reads payslips, asks HR",
  manager: "Also approves the team's leave",
  hr: "Also answers HR requests; payslip views are logged",
};

export default function Login() {
  const router = useRouter();
  const { data: options, error } = useSWR<{ demo: boolean }>("/login-options", fetcher);

  async function signedIn() {
    await mutate(() => true, undefined, { revalidate: false });
    router.replace("/");
  }

  return (
    <main className="min-h-screen bg-paper px-4 py-12 sm:py-20">
      <div className="mx-auto max-w-[560px]">
        <div className="flex items-center gap-2">
          <span aria-hidden className="grid h-8 w-8 place-items-center rounded bg-dossier text-white font-semibold">C</span>
          <span className="text-md font-semibold">CognivexAI</span>
        </div>
        <h1 className="mt-10 text-xl font-semibold tracking-[-0.01em]">Sign in</h1>
        {error && <div className="mt-6"><ErrorNote message="Can't reach the server. Start the API with: uvicorn server:app --port 8000" /></div>}
        <PasswordForm onDone={signedIn} />
        {options?.demo && <DemoPicker onDone={signedIn} />}
      </div>
    </main>
  );
}

function PasswordForm({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api("/session", { body: { email, password } });
      onDone();
    } catch (x) {
      setErr((x as Error).message);
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-6 max-w-[440px] space-y-4">
      <label className="block">
        <span className="label">Work email</span>
        <input className="field" type="email" autoComplete="username" required autoFocus value={email}
               onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label className="block">
        <span className="label">Password</span>
        <input className="field" type="password" autoComplete="current-password" required value={password}
               onChange={(e) => setPassword(e.target.value)} />
      </label>
      {err && <ErrorNote message={err} />}
      <button className="btn-primary h-11 w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      <p className="text-sm text-ink-soft">No password yet, or forgot it? Ask HR to send you a sign-in link.</p>
    </form>
  );
}

function DemoPicker({ onDone }: { onDone: () => void }) {
  const { data, error } = useSWR<DemoUser[]>("/demo-users", fetcher);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState("");
  const [q, setQ] = useState("");

  async function choose(id: string) {
    setBusy(id);
    try {
      await api("/session", { body: { employee_id: id } });
      onDone();
    } catch (e) {
      setFailed((e as Error).message);
      setBusy(null);
    }
  }

  const search = q.trim().toLowerCase();
  const shown = search
    ? (data ?? []).filter((u) => `${u.name} ${u.title} ${u.department}`.toLowerCase().includes(search)).slice(0, 8)
    : FEATURED.map((id) => data?.find((u) => u.id === id)).filter((u): u is DemoUser => !!u);

  return (
    <section aria-labelledby="demo" className="mt-12 border-t border-rule pt-8">
      <h2 id="demo" className="font-medium">Or try the demo as someone</h2>
      <p className="mt-1 text-sm text-ink-soft max-w-[52ch]">
        Demo mode is on, so no password is needed here. Everything you see and do, including what the assistant can access,
        follows the person you choose.
      </p>
      <input className="field mt-4" placeholder={`Search ${data?.length ?? ""} people by name, job or team`}
             value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
      <ul className="mt-3 panel divide-y divide-rule overflow-hidden">
        {error && <li className="p-4"><ErrorNote message={error.message} /></li>}
        {!data && !error && <li className="p-4 text-ink-soft">Loading people…</li>}
        {data && shown.length === 0 && <li className="p-4 text-ink-soft">No one matches &ldquo;{q}&rdquo;.</li>}
        {shown.map((u) => (
          <li key={u.id}>
            <button onClick={() => choose(u.id)} disabled={!!busy}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-paper disabled:opacity-60">
              <Avatar name={u.name} size={36} />
              <span className="flex-1 min-w-0">
                <span className="block font-medium">{u.name}<span className="ml-2 font-normal text-ink-soft">{u.title}</span></span>
                <span className="block text-sm text-ink-soft truncate">{ROLE_NOTE[u.role]}</span>
              </span>
              <span className="text-sm text-dossier font-medium">{busy === u.id ? "Signing in…" : "Continue"}</span>
            </button>
          </li>
        ))}
      </ul>
      {failed && <div className="mt-4"><ErrorNote message={failed} /></div>}
    </section>
  );
}
