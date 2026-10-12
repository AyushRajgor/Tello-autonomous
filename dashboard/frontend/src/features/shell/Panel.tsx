import type { LucideIcon } from "lucide-react";
import type * as React from "react";

import { cn } from "@/lib/utils";

/** A labelled console panel: mono caps title, optional right-hand status. */
export function Panel({
  title,
  icon: Icon,
  aside,
  className,
  bodyClassName,
  children,
  ...props
}: {
  title: string;
  icon?: LucideIcon;
  aside?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  children: React.ReactNode;
} & Omit<React.ComponentProps<"section">, "title" | "children">) {
  return (
    <section aria-label={title} className={cn("panel flex flex-col", className)} {...props}>
      <header className="flex h-9 shrink-0 items-center gap-2 border-b px-3">
        {Icon ? <Icon className="size-3.5 text-hud" /> : null}
        <h2 className="font-mono text-[10px] font-medium tracking-[0.18em] text-muted-foreground uppercase">{title}</h2>
        {aside ? <div className="ml-auto flex items-center gap-2">{aside}</div> : null}
      </header>
      <div className={cn("p-3", bodyClassName)}>{children}</div>
    </section>
  );
}
