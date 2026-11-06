"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useRef, useState } from "react";
import useSWR, { mutate } from "swr";
import {
  Bell, BookOpen, CalendarCog, CalendarDays, CheckSquare, Contact, Home, Inbox, Menu, MessageCircleQuestion,
  MessageSquare, Receipt, ShieldCheck, Users, X,
} from "lucide-react";
import { api, ApiError, fetcher } from "@/lib/api";
import { initials, readable, timestamp } from "@/lib/format";
import { Notification, Person } from "@/lib/types";
import { MeContext } from "./Me";
import { Assistant } from "./Assistant";

type NavItem = { href: string; label: string; icon: typeof Home; badge?: number };

function navFor(me: Person, pending: number, openTickets: number): { title: string; items: NavItem[] }[] {
  const groups: { title: string; items: NavItem[] }[] = [{
    title: "Me",
    items: [
      { href: "/", label: "Home", icon: Home },
      { href: "/time-off", label: "Time off", icon: CalendarDays },
      { href: "/payslips", label: "Payslips", icon: Receipt },
      { href: "/requests", label: "HR requests", icon: MessageSquare },
    ],
  }];
  if (me.role === "manager") groups.push({ title: "My team", items: [
    { href: "/approvals", label: "Approvals", icon: CheckSquare, badge: pending },
    { href: "/team", label: "Team", icon: Users },
  ] });
  if (me.role === "hr") groups.push({ title: "HR", items: [
    { href: "/inbox", label: "Request inbox", icon: Inbox, badge: openTickets },
    { href: "/access-log", label: "Payslip access", icon: ShieldCheck },
    { href: "/leave-admin", label: "Leave settings", icon: CalendarCog },
  ] });
  groups.push({ title: "Company", items: [
    { href: "/people", label: "People", icon: Contact },
    { href: "/handbook", label: "Handbook", icon: BookOpen },
  ] });
  return groups;
}

export function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const { data: me, error } = useSWR<Person>("/me", fetcher);
  const { data: home } = useSWR<{ pending_approvals?: number; open_hr_tickets?: number }>(me ? "/home" : null, fetcher);
  const [menuOpen, setMenuOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) router.replace("/login");
  }, [error, router]);
  useEffect(() => setMenuOpen(false), [path]);

  if (!me) {
    return <div className="min-h-screen grid place-items-center text-ink-soft">{error && !(error instanceof ApiError && error.status === 401) ? "Can't reach the server. Is the API running on port 8000?" : "Loading…"}</div>;
  }

  const groups = navFor(me, home?.pending_approvals ?? 0, home?.open_hr_tickets ?? 0);
  const nav = (
    <nav aria-label="Main" className="space-y-6">
      {groups.map((g) => (
        <div key={g.title}>
          <p className="px-3 mb-1 text-xs font-medium text-ink-faint">{g.title}</p>
          <ul className="space-y-0.5">
            {g.items.map(({ href, label, icon: Icon, badge }) => {
              const active = href === "/" ? path === "/" : path.startsWith(href);
              return (
                <li key={href}>
                  <Link href={href} aria-current={active ? "page" : undefined}
                        className={`flex items-center gap-2.5 rounded px-3 h-9 ${
                          active ? "bg-dossier-tint text-dossier font-medium" : "text-ink-soft hover:text-ink hover:bg-paper"}`}>
                    <Icon size={16} strokeWidth={1.75} aria-hidden />
                    <span className="flex-1">{label}</span>
                    {!!badge && <span className="min-w-5 rounded-full bg-dossier px-1.5 text-center text-xs font-medium leading-5 text-white">{badge}</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );

  return (
    <MeContext.Provider value={me}>
      <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
        <div className="app-chrome hidden lg:block border-r border-rule bg-surface">
          <aside className="sticky top-0 flex h-screen flex-col px-3 py-5">
            <Brand />
            <div className="mt-7 flex-1 overflow-y-auto">{nav}</div>
            <p className="px-3 text-xs text-ink-faint">Demo data. No real employees.</p>
          </aside>
        </div>

        <div className={`min-w-0 transition-[padding] ${askOpen ? "xl:pr-[420px]" : ""}`}>
          <header className="app-chrome sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-rule bg-surface/95 backdrop-blur px-4 sm:px-6">
            <button className="btn-ghost h-9 w-9 px-0 lg:hidden" onClick={() => setMenuOpen(!menuOpen)} aria-label="Menu" aria-expanded={menuOpen}>
              {menuOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            <div className="lg:hidden"><Brand compact /></div>
            <div className="ml-auto flex items-center gap-1">
              <button className={`btn h-9 ${askOpen ? "bg-dossier-tint text-dossier" : "btn-quiet"}`} onClick={() => setAskOpen(!askOpen)} aria-expanded={askOpen} aria-label="Ask HR assistant">
                <MessageCircleQuestion size={16} strokeWidth={1.75} aria-hidden />
                <span className="hidden sm:inline">Ask HR assistant</span>
              </button>
              <Notifications />
              <PersonMenu me={me} />
            </div>
          </header>
          {menuOpen && <div className="app-chrome lg:hidden border-b border-rule bg-surface px-3 py-4">{nav}</div>}
          <main className="mx-auto max-w-[1080px] px-4 sm:px-6 py-6 sm:py-8">{children}</main>
        </div>

        <Assistant open={askOpen} onClose={() => setAskOpen(false)} />
      </div>
    </MeContext.Provider>
  );
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2 px-3">
      <span aria-hidden className="grid h-7 w-7 place-items-center rounded bg-dossier text-white text-sm font-semibold">C</span>
      {!compact && <span className="font-semibold tracking-[-0.01em]">CognivexAI</span>}
    </Link>
  );
}

function useOutside(ref: React.RefObject<HTMLElement>, onOut: () => void) {
  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && onOut();
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [ref, onOut]);
}

function Notifications() {
  const { data } = useSWR<Notification[]>("/notifications", fetcher);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false));
  const unread = data?.filter((n) => !n.is_read).length ?? 0;

  async function toggle() {
    setOpen(!open);
    if (!open && unread) { await api("/notifications/read", { body: {} }); mutate("/notifications"); }
  }
  return (
    <div ref={ref} className="relative">
      <button className="btn-ghost h-9 w-9 px-0 relative" onClick={toggle} aria-label={`Notifications${unread ? `, ${unread} new` : ""}`} aria-expanded={open}>
        <Bell size={17} strokeWidth={1.75} />
        {unread > 0 && <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-no ring-2 ring-surface" />}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-[340px] max-w-[calc(100vw-2rem)] panel shadow-[0_12px_40px_-12px_rgba(26,30,41,0.3)]">
          <p className="px-4 py-3 border-b border-rule font-medium">Notifications</p>
          <ul className="max-h-[360px] overflow-y-auto divide-y divide-rule">
            {!data?.length && <li className="px-4 py-6 text-center text-ink-soft">Nothing new. Decisions on your requests will show up here.</li>}
            {data?.map((n) => (
              <li key={n.id} className="px-4 py-3">
                <p className="text-sm">{readable(n.message)}</p>
                <p className="mt-1 text-xs text-ink-faint">{timestamp(n.created_at)}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function PersonMenu({ me }: { me: Person }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false));
  async function signOut() {
    await api("/session", { method: "DELETE" });
    await mutate(() => true, undefined, { revalidate: false });
    router.replace("/login");
  }
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`Signed in as ${me.name}`}
              className="flex items-center gap-2 rounded pl-1 pr-2 h-9 hover:bg-paper">
        <Avatar name={me.name} />
        <span className="hidden md:block text-left leading-tight">
          <span className="block text-sm font-medium">{me.name}</span>
          <span className="block text-xs text-ink-soft">{me.title}</span>
        </span>
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-56 panel py-1 shadow-[0_12px_40px_-12px_rgba(26,30,41,0.3)]">
          <p className="px-3 py-2 text-xs text-ink-soft">{me.email}</p>
          <button className="w-full text-left px-3 py-2 hover:bg-paper" onClick={signOut}>Switch person</button>
        </div>
      )}
    </div>
  );
}

export function Avatar({ name, size = 28 }: { name: string; size?: number }) {
  let h = 2166136261;                                 // FNV-1a: each name keeps its own muted hue
  for (const c of name) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
  const tone = `hsl(${h % 360} 32% 38%)`;
  return (
    <span aria-hidden className="grid shrink-0 place-items-center rounded-full text-white font-medium"
          style={{ width: size, height: size, background: tone, fontSize: size * 0.4 }}>
      {initials(name)}
    </span>
  );
}
