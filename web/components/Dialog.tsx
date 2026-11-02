"use client";
import { X } from "lucide-react";
import { ReactNode, useEffect, useRef } from "react";

export function Dialog({ open, onClose, title, children, width = "max-w-lg" }:
  { open: boolean; onClose: () => void; title: string; children: ReactNode; width?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-ink/30 p-0 sm:p-4" onMouseDown={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}
           className={`w-full ${width} bg-surface rounded-t-lg sm:rounded-lg border border-rule shadow-[0_12px_40px_-12px_rgba(26,30,41,0.35)]`}>
        <div className="flex items-center justify-between px-5 pt-4 pb-3">
          <h2 className="text-md font-semibold">{title}</h2>
          <button onClick={onClose} className="btn-ghost h-8 w-8 px-0" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="px-5 pb-5">{children}</div>
      </div>
    </div>
  );
}
