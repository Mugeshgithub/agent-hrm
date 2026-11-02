import { ReactNode } from "react";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between mb-6">
      <div>
        <h1 className="text-xl font-semibold tracking-[-0.01em]">{title}</h1>
        {description && <p className="mt-1 text-ink-soft max-w-[62ch]">{description}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-5 py-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-ink-soft">{children}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return <p role="alert" className="rounded border border-[#EBC9C4] bg-[#FBF1EF] px-3 py-2 text-sm text-no">{message}</p>;
}
