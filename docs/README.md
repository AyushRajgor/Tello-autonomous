# TelloAI Documentation

Technical reference for the TelloAI drone agent. Each doc is self-contained and grounded in the actual source.

## What this project is

TelloAI is a voice- and text-controllable autonomous drone agent built on a DJI Tello. An LLM is both the planner (deciding what to do) and the perception engine (interpreting the camera feed). There is no local ML, no SLAM, no hardcoded command list — the model calls typed Python functions exposed by `TelloToolkit` and the drone executes them.

Two agent paths coexist in the codebase:

| Path | Model | When used | Latency |
|---|---|---|---|
| **Live** | `gemini-3.1-flash-live-preview` | `--voice`, `--voice-text`, `--dashboard` (default) | 0.5–1.5 s first token |
| **Legacy** | `gemini-3.5-flash` + `gemini-3.1-flash-lite` | `--dashboard --legacy-agent`, default text mode | 8–15 s per full turn |

The Live path streams audio + drone video over a persistent WebSocket and handles perception inside the model. The legacy path makes separate API calls for planning, vision, and synthesis; it is slower but easier to trace span by span.

## Documents

| File | Read when you want to… |
|---|---|
| [architecture.md](./architecture.md) | Understand the module layout, external dependencies, and runtime shape of the system |
| [workflow.md](./workflow.md) | Trace a user request end to end — Live API turn, legacy agent loop, dashboard flows, tool execution |
| [user-flows.md](./user-flows.md) | See exactly what happens for each CLI mode (`--voice`, `--dashboard`, `--simulate`, etc.) |
| [components.md](./components.md) | Look up a specific module (`toolkit.py`, `safety.py`, `gemini_live.py`, etc.) |
| [safety.md](./safety.md) | Understand safety checks, thresholds, failure modes, and the flight state machine |

## Start here depending on what you're doing

- **Running it for the first time** → [user-flows.md](./user-flows.md)
- **Adding a new drone tool** → [components.md](./components.md) → `core/toolkit.py` section
- **Debugging slow responses** → [workflow.md](./workflow.md) → "Live vs Legacy latency"
- **Adding safety checks** → [safety.md](./safety.md)
- **Exploring the overall system** → [architecture.md](./architecture.md)

## Conventions used in these docs

- Code references look like `` `12:14:core/toolkit.py` `` — start line, end line, path from repo root. Clickable in Cursor.
- Diagrams are [Mermaid](https://mermaid.js.org/) — render natively on GitHub and in most IDEs.
- "Live path" and "Legacy path" are used consistently to distinguish the two agent implementations.
- File paths use forward slashes regardless of OS.
- Anything "not yet implemented" is flagged explicitly.

## See also

- [README.md](../README.md) — quick-start instructions
- [prd.md](../prd.md) — original product design document
- [config.py](../config.py) — single source of truth for models, thresholds, and tool schemas
