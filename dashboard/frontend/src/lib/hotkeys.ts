import * as React from "react";

/**
 * Operator keyboard shortcuts:
 *   Space  → STOP (hover in place, AI paused)
 *   Escape → Land
 *
 * Ignored while the user is typing (inputs, textareas, contenteditable) and
 * inside open overlays (popovers, dialogs, menus), which own the keyboard.
 */
export type OperatorHotkey = "hover" | "land";

export const OPERATOR_HOTKEYS: ReadonlyArray<{ key: string; action: OperatorHotkey; label: string }> = [
  { key: "Space", action: "hover", label: "STOP" },
  { key: "Esc", action: "land", label: "Land" }
];

// <input> types that take typed text (Space/Escape belong to the field).
const NON_TEXT_INPUT_TYPES = new Set(["button", "submit", "reset", "checkbox", "radio", "range", "color", "file", "image"]);

const OVERLAY_SELECTOR =
  '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]';

const EDITABLE_SELECTOR = '[contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]';

function asElement(target: EventTarget | null): HTMLElement | null {
  if (!target || typeof (target as Element).closest !== "function") {
    return null;
  }
  return target as HTMLElement;
}

export function isTypingTarget(target: EventTarget | null): boolean {
  const el = asElement(target);
  if (!el) {
    return false;
  }
  if (el.isContentEditable) {
    return true;
  }
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  if (tag === "INPUT") {
    const type = ((el as HTMLInputElement).type || "text").toLowerCase();
    return !NON_TEXT_INPUT_TYPES.has(type);
  }
  return el.closest(EDITABLE_SELECTOR) !== null;
}

export function isInsideOverlay(target: EventTarget | null): boolean {
  const el = asElement(target);
  return el ? el.closest(OVERLAY_SELECTOR) !== null : false;
}

export type HotkeyEventLike = Pick<KeyboardEvent, "key" | "code" | "target" | "ctrlKey" | "metaKey" | "altKey"> & {
  isComposing?: boolean;
};

/** Map a key event to an operator action, or null if it isn't ours to handle. */
export function resolveOperatorHotkey(event: HotkeyEventLike): OperatorHotkey | null {
  if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) {
    return null;
  }
  if (isTypingTarget(event.target) || isInsideOverlay(event.target)) {
    return null;
  }
  if (event.key === " " || event.key === "Spacebar" || event.code === "Space") {
    return "hover";
  }
  if (event.key === "Escape" || event.key === "Esc") {
    return "land";
  }
  return null;
}

/**
 * Global operator hotkeys. Listens in the capture phase so focused widgets
 * can't swallow the key, and cancels Space so a focused button (e.g.
 * "Forward") is not also activated by the same keypress.
 */
export function useOperatorHotkeys(handlers: { onStop: () => void; onLand: () => void }, enabled = true) {
  const handlersRef = React.useRef(handlers);

  React.useEffect(() => {
    handlersRef.current = handlers;
  });

  React.useEffect(() => {
    if (!enabled || typeof window === "undefined") {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      const action = resolveOperatorHotkey(event);
      if (!action) {
        return;
      }
      event.preventDefault();
      if (event.repeat) {
        return;
      }
      if (action === "hover") {
        handlersRef.current.onStop();
      } else {
        handlersRef.current.onLand();
      }
    };

    // Firefox activates a focused button on Space keyup; cancel that too.
    const onKeyUp = (event: KeyboardEvent) => {
      if (resolveOperatorHotkey(event) === "hover") {
        event.preventDefault();
      }
    };

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [enabled]);
}
