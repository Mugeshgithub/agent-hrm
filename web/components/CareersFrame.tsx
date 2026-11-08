"use client";
// The frame of the public careers pages: no menu, no sign-in, nothing from the signed-in app. One column that works on a phone.
import Link from "next/link";
import { ReactNode } from "react";

export function CareersFrame({ children }: { children: ReactNode }) {
  return (
    <main className="min-h-screen bg-paper">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-[760px] items-center justify-between px-4 py-3">
          <Link href="/careers" className="flex items-center gap-2">
            <span aria-hidden className="grid h-8 w-8 place-items-center rounded bg-dossier font-semibold text-white">C</span>
            <span className="text-md font-semibold">CognivexAI <span className="font-normal text-ink-soft">Careers</span></span>
          </Link>
        </div>
      </header>
      <div className="mx-auto max-w-[760px] px-4 py-6 sm:py-10">{children}</div>
    </main>
  );
}
