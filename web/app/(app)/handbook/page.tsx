"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";

type Section = { title: string; text: string };
const slug = (s: string) => s.toLowerCase().normalize("NFD").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");

export default function Handbook() {
  const { data } = useSWR<Section[]>("/handbook", fetcher);
  return (
    <>
      <PageHeader title="Handbook" description="The company's HR policy. The assistant answers from this text, and from the Code du travail for legal questions." />
      <div className="grid gap-8 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Sections" className="hidden lg:block">
          <ul className="sticky top-20 space-y-1 text-sm">
            {data?.map((s) => <li key={s.title}><a href={`#${slug(s.title)}`} className="block py-1 text-ink-soft hover:text-dossier">{s.title}</a></li>)}
          </ul>
        </nav>
        <article className="panel px-6 py-6 sm:px-8 space-y-8 max-w-[760px]">
          {data?.map((s) => (
            <section key={s.title} id={slug(s.title)} className="scroll-mt-20">
              <h2 className="text-md font-semibold">{s.title}</h2>
              <p className="mt-2 leading-relaxed max-w-[68ch]">{s.text}</p>
            </section>
          ))}
        </article>
      </div>
    </>
  );
}
