import type * as React from "react";

import { cn } from "@/lib/utils";

/** A titled surface. Grouping comes from space; the title is a real heading. */
export function Panel({
  title,
  description,
  aside,
  className,
  children,
  ...props
}: {
  title: string;
  description?: string;
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
} & Omit<React.ComponentProps<"section">, "title" | "children">) {
  return (
    <section aria-label={title} className={cn("surface flex flex-col gap-4 p-4", className)} {...props}>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          {description ? <p className="mt-0.5 text-[13px] leading-snug text-muted-foreground">{description}</p> : null}
        </div>
        {aside ? <div className="shrink-0">{aside}</div> : null}
      </header>
      {children}
    </section>
  );
}
