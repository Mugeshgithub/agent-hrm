"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { Avatar } from "@/components/Shell";
import { ErrorNote } from "@/components/PageHeader";

type DemoUser = { id: string; name: string; role: string; title: string; department: string };

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

  const order = ["employee", "manager", "hr"];
  const sorted = [...(data ?? [])].sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role) || a.name.localeCompare(b.name));

  return (
    <main className="min-h-screen bg-paper px-4 py-12 sm:py-20">
      <div className="mx-auto max-w-[560px]">
        <div className="flex items-center gap-2">
          <span aria-hidden className="grid h-8 w-8 place-items-center rounded bg-dossier text-white font-semibold">A</span>
          <span className="text-md font-semibold">Agent HRM</span>
        </div>
        <h1 className="mt-10 text-xl font-semibold tracking-[-0.01em]">Who's signing in?</h1>
        <p className="mt-2 text-ink-soft max-w-[52ch]">
          This is a demo with sample people, so there's no password. Everything you see and do,
          including what the assistant can access, follows the person you choose.
        </p>

        <ul className="mt-8 panel divide-y divide-rule overflow-hidden">
          {error && <li className="p-4"><ErrorNote message="Can't reach the server. Start the API with: uvicorn server:app --port 8000" /></li>}
          {!data && !error && <li className="p-4 text-ink-soft">Loading people…</li>}
          {sorted.map((u) => (
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
