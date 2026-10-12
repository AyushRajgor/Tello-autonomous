import { describe, expect, it } from "vitest";

import { getDashboardToken, withToken } from "./api";
import { isTypingTarget, resolveOperatorHotkey, type HotkeyEventLike } from "./hotkeys";

function keyEvent(key: string, target: EventTarget | null, extra: Partial<HotkeyEventLike> = {}): HotkeyEventLike {
  return {
    key,
    code: key === " " ? "Space" : key,
    target,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    ...extra
  };
}

describe("operator hotkeys", () => {
  it("maps Space to STOP and Escape to land on non-typing targets", () => {
    const button = document.createElement("button");
    expect(resolveOperatorHotkey(keyEvent(" ", document.body))).toBe("hover");
    expect(resolveOperatorHotkey(keyEvent(" ", button))).toBe("hover");
    expect(resolveOperatorHotkey(keyEvent("Escape", document.body))).toBe("land");
    expect(resolveOperatorHotkey(keyEvent("a", document.body))).toBeNull();
  });

  it("ignores keys while typing in inputs, textareas and contenteditable", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    const child = document.createElement("span");
    editable.appendChild(child);

    for (const target of [input, textarea, editable, child]) {
      expect(resolveOperatorHotkey(keyEvent(" ", target))).toBeNull();
      expect(resolveOperatorHotkey(keyEvent("Escape", target))).toBeNull();
    }
  });

  it("does not treat checkbox-like inputs as typing", () => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    expect(isTypingTarget(checkbox)).toBe(false);
  });

  it("ignores modified keys, IME composition and open overlays", () => {
    expect(resolveOperatorHotkey(keyEvent(" ", document.body, { ctrlKey: true }))).toBeNull();
    expect(resolveOperatorHotkey(keyEvent(" ", document.body, { isComposing: true }))).toBeNull();

    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const inner = document.createElement("button");
    dialog.appendChild(inner);
    expect(resolveOperatorHotkey(keyEvent("Escape", inner))).toBeNull();
  });
});

describe("dashboard token passthrough", () => {
  it("reads the token from the page query string", () => {
    expect(getDashboardToken("?token=abc123")).toBe("abc123");
    expect(getDashboardToken("?foo=1")).toBeNull();
    expect(getDashboardToken("")).toBeNull();
  });

  it("appends the token to API URLs", () => {
    expect(withToken("/api/traces?limit=50", "s3")).toBe("/api/traces?limit=50&token=s3");
    expect(withToken("/video-status", "a&b")).toBe("/video-status?token=a%26b");
    expect(withToken("/video-status", null)).toBe("/video-status");
  });
});
