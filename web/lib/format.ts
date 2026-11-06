// dates arrive as ISO strings (2026-11-18); show them the way people say them
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function parseISO(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function toISO(d: Date) {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function day(iso: string) {
  const d = parseISO(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

export function dayWithWeekday(iso: string) {
  const d = parseISO(iso);
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

export function range(start: string, end: string) {
  if (start === end) return dayWithWeekday(start);
  const a = parseISO(start), b = parseISO(end);
  if (a.getMonth() === b.getMonth()) return `${a.getDate()}–${b.getDate()} ${MONTHS[b.getMonth()]}`;
  return `${day(start)} – ${day(end)}`;
}

export function monthName(ym: string) {
  const [y, m] = ym.split("-").map(Number);
  return `${LONG_MONTHS[m - 1]} ${y}`;
}

export function daysUntil(iso: string) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((parseISO(iso).getTime() - today.getTime()) / 86400000);
}

export function relative(iso: string) {
  const n = daysUntil(iso);
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n > 1) return `in ${n} days`;
  return `${-n} days ago`;
}

export function timestamp(sql: string) {
  // "2026-11-02 11:32:00" (UTC from SQLite) -> "2 Nov, 12:32" local
  const d = new Date(sql.replace(" ", "T") + "Z");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export const leaveLabel = (t: string) =>
  t === "casual" ? "Casual leave" : t === "sick" ? "Sick leave" : t === "floater" ? "Floating holiday" : t;

export function initials(name: string) {
  return name.split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
}

// messages from the server contain ISO dates ("from 2026-11-18 to 2026-11-19"); show them as "18 Nov"
export function readable(message: string) {
  return message
    .replace(/from (\d{4}-\d{2}-\d{2}) to \1/g, "on $1")          // one-day leave: "on 9 Nov"
    .replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (iso) => day(iso));
}

// one minus sign everywhere in money: "-€147.00" and "−€320.00" both become "−€…"
export function money(eur: string, negative = false) {
  const plain = eur.replace(/^[-−]/, "");
  return negative || /^[-−]/.test(eur) ? `−${plain}` : plain;
}
