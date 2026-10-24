import { ChevronDown } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

const OPEN_KEY_PREFIX = "telloai.panel.";

function readOpen(id: string | undefined): boolean {
  if (!id) {
    return true;
  }
  try {
    return localStorage.getItem(OPEN_KEY_PREFIX + id) !== "closed";
  } catch {
    return true;
  }
}

/** Open or closed, remembered in this browser. */
function useRememberedOpen(id: string | undefined) {
  const [open, setOpen] = React.useState(() => readOpen(id));
  const toggle = React.useCallback(() => {
    const next = !open;
    setOpen(next);
    if (id) {
      try {
        localStorage.setItem(OPEN_KEY_PREFIX + id, next ? "open" : "closed");
      } catch {
        // ignore storage failures
      }
    }
  }, [id, open]);
  return [open, toggle] as const;
}

/**
 * A titled surface. Grouping comes from space; the title is a real heading.
 *
 * Pass `collapsibleId` to let the operator fold the body away. The choice is
 * remembered per browser and never changes on its own, so controls don't move
 * under the pointer mid-flight. Never make the Safety panel collapsible.
 */
export function Panel({
  title,
  description,
  aside,
  collapsibleId,
  className,
  children,
  ...props
}: {
  title: string;
  description?: string;
  aside?: React.ReactNode;
  collapsibleId?: string;
  className?: string;
  children: React.ReactNode;
} & Omit<React.ComponentProps<"section">, "title" | "children">) {
  const [open, toggle] = useRememberedOpen(collapsibleId);
  const bodyId = React.useId();

  if (collapsibleId === undefined) {
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

  return (
    <section aria-label={title} className={cn("surface flex flex-col gap-4 p-4", className)} {...props}>
      <header>
        {/* The button's ::after stretches over the whole title row, so the badge and chevron toggle too. */}
        <div className="group relative flex items-start justify-between gap-3">
          <h2 className="min-w-0 text-sm font-semibold">
            <button
              type="button"
              aria-expanded={open}
              aria-controls={bodyId}
              onClick={toggle}
              className="cursor-pointer text-start outline-none after:absolute after:-inset-x-2 after:-inset-y-2.5 after:rounded-lg focus-visible:after:ring-[3px] focus-visible:after:ring-ring/50"
            >
              {title}
            </button>
          </h2>
          <div className="flex min-h-5 shrink-0 items-center gap-2">
            {aside}
            <ChevronDown
              aria-hidden="true"
              className={cn(
                "size-4 text-muted-foreground group-hover:text-foreground motion-safe:transition-[rotate,color] motion-safe:duration-150 motion-safe:ease-out",
                open && "rotate-180"
              )}
            />
          </div>
        </div>
        {description && open ? <p className="mt-0.5 text-[13px] leading-snug text-muted-foreground">{description}</p> : null}
      </header>
      <div id={bodyId} hidden={!open}>
        {children}
      </div>
    </section>
  );
}
