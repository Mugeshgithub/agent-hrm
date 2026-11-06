"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
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
  const { data, error } = useSWR<DemoUser[]>("/demo-users", fetcher);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState("");
  const [q, setQ] = useState("");

  async function choose(id: string) {
    setBusy(id);
    try {
      await api("/session", { body: { employee_id: id } });
      await mutate(() => true, undefined, { revalidate: false });
      router.replace("/");
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
    <main className="min-h-screen bg-paper px-4 py-12 sm:py-20">
      <div className="mx-auto max-w-[560px]">
        <div className="flex items-center gap-2">
          <span aria-hidden className="grid h-8 w-8 place-items-center rounded bg-dossier text-white font-semibold">C</span>
          <span className="text-md font-semibold">CognivexAI</span>
        </div>
        <h1 className="mt-10 text-xl font-semibold tracking-[-0.01em]">Who's signing in?</h1>
        <p className="mt-2 text-ink-soft max-w-[52ch]">
          This is a demo with sample people, so there's no password. Everything you see and do,
          including what the assistant can access, follows the person you choose.
        </p>

        <input className="field mt-8" placeholder={`Search ${data?.length ?? ""} people by name, job or team`}
               value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
        <ul className="mt-3 panel divide-y divide-rule overflow-hidden">
          {error && <li className="p-4"><ErrorNote message="Can't reach the server. Start the API with: uvicorn server:app --port 8000" /></li>}
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
      </div>
    </main>
  );
}
