import { PENDING } from "@/lib/types";

const STATUS: Record<string, { label: string; color: string }> = {
  approved: { label: "Approved", color: "bg-ok" },
  [PENDING]: { label: "Waiting for approval", color: "bg-wait" },
  rejected: { label: "Rejected", color: "bg-no" },
  cancelled: { label: "Withdrawn", color: "bg-off" },
  open: { label: "Open", color: "bg-wait" },
  closed: { label: "Answered", color: "bg-ok" },
};

export function Status({ value }: { value: string }) {
  const s = STATUS[value] ?? { label: value, color: "bg-off" };
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-ink whitespace-nowrap">
      <span className={`h-2 w-2 rounded-full ${s.color}`} aria-hidden />
      {s.label}
    </span>
  );
}
