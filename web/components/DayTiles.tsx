import { Balance } from "@/lib/types";

// A balance is drawn as one tile per day: filled = booked (approved, still ahead), striped = waiting
// for approval, open = still available. A part month of paid leave is pro-rated, so a balance can end
// in a fraction of a day (2.34), drawn as one tile as wide as that fraction.
export function DayTiles({ balance, booked = 0, size = "md" }: { balance: Balance; booked?: number; size?: "sm" | "md" }) {
  const available = Math.max(balance.available, 0);
  const whole = Math.floor(available);
  const fraction = Math.round((available - whole) * 100) / 100;
  const tiles = [
    ...Array(booked).fill("booked"),
    ...Array(balance.pending).fill("pending"),
    ...Array(whole).fill("open"),
    ...(fraction > 0 ? ["part"] : []),
  ] as const;
  const dim = size === "sm" ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  const px = size === "sm" ? 14 : 18;
  const partWidth = Math.max(3, Math.round(px * fraction));   // never thinner than a sliver you can see
  return (
    <div className="flex flex-wrap gap-[3px]" role="img"
         aria-label={`${available} days available, ${balance.pending} waiting for approval, ${booked} booked`}>
      {tiles.length === 0 && <span className="text-sm text-ink-faint">No days left</span>}
      {tiles.map((t, i) => (
        <span key={i} style={t === "part" ? { width: partWidth } : undefined}
              className={`${t === "part" ? (size === "sm" ? "h-3.5" : "h-[18px]") : dim} rounded-[3px] ${
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
