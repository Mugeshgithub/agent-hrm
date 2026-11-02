import { Balance } from "@/lib/types";

// Leave is counted in whole days, so a balance is drawn as one tile per day:
// filled = booked (approved, still ahead), striped = waiting for approval, open = still available.
export function DayTiles({ balance, booked = 0, size = "md" }: { balance: Balance; booked?: number; size?: "sm" | "md" }) {
  const available = Math.max(balance.available, 0);
  const tiles = [
    ...Array(booked).fill("booked"),
    ...Array(balance.pending).fill("pending"),
    ...Array(available).fill("open"),
  ] as const;
  const dim = size === "sm" ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  return (
    <div className="flex flex-wrap gap-[3px]" role="img"
         aria-label={`${available} days available, ${balance.pending} waiting for approval, ${booked} booked`}>
      {tiles.length === 0 && <span className="text-sm text-ink-faint">No days left</span>}
      {tiles.map((t, i) => (
        <span key={i} className={`${dim} rounded-[3px] ${
          t === "booked" ? "bg-dossier" : t === "pending" ? "tile-pending" : "border border-rule-strong bg-surface"}`} />
      ))}
    </div>
  );
}

export function TileLegend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft">
      <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[2px] bg-dossier" />Booked</span>
      <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[2px] tile-pending" />Waiting for approval</span>
      <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[2px] border border-rule-strong" />Available</span>
    </div>
  );
}
