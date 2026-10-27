import type { ReactNode } from "react";

/** A bordered box with a title bar, the basic building block of the layout. */
export function Panel({
  title,
  right,
  children,
  className = "",
  bodyClassName = "",
  accent = false,
}: {
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  accent?: boolean;
}) {
  return (
    <section className={`flex min-w-0 flex-col border border-rule bg-panel ${className}`}>
      <header
        className={`flex flex-wrap items-center justify-between gap-x-3 border-b border-rule px-2.5 py-1 ${
          accent ? "bg-accent-bg" : "bg-panel-head"
        }`}
      >
        <h2 className={`font-bold break-all ${accent ? "text-accent" : "text-ink"}`}>{title}</h2>
        {right && <div className="text-ink-3">{right}</div>}
      </header>
      <div className={`min-w-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}
