# TelloAI Documentation

Technical reference for the TelloAI drone agent. Each doc is self-contained and grounded in the actual source.

## What this project is

TelloAI is a voice- and text-controllable autonomous drone agent built on a DJI Tello. An LLM is both the planner (deciding what to do) and the perception engine (interpreting the camera feed). There is no local ML, no SLAM, no hardcoded command list. The model calls typed Python functions exposed by `TelloToolkit`, and the toolkit enforces the safety limits in code before anything reaches the drone. A human operator can always stop, land or cut the motors without going through the model.

Two agent paths coexist in the codebase:

| Path | Model | When used | Notes |
|---|---|---|---|
| **Live** | `gemini-3.8-live` | `--voice`, `--voice-text`, `--dashboard` (default) | Persistent session, spoken replies, sees drone video (≤1 FPS) |
| **Text agent** (legacy) | `gemini-3.8-flash` | default text mode, `--dashboard --legacy-agent` | One `generateContent` call per loop step; richest traces |

The Live path streams audio and drone video over a persistent WebSocket and handles perception inside the model. The text agent calls the model in a function-calling loop; when it calls `get_frame`, the JPEG goes straight back to the model inside the function response, so it looks at the camera itself.

## Documents

| File | Read when you want to… |
|---|---|
| [architecture.md](./architecture.md) | Understand the module layout, threads, external services and the shape of the system |
| [workflow.md](./workflow.md) | Trace a request end to end: Live turn, text-agent loop, push-to-talk, operator commands, tool execution, tracing |
| [user-flows.md](./user-flows.md) | See exactly what happens for each CLI mode (`--voice`, `--dashboard`, `--simulate`, etc.) |
| [components.md](./components.md) | Look up a specific module (`toolkit.py`, `safety.py`, `watchdog.py`, `gemini_live.py`, etc.) |
| [safety.md](./safety.md) | Understand limits, the operator hold, the watchdog, failure modes and known gaps |

## Start here depending on what you're doing

- **Running it for the first time** → [user-flows.md](./user-flows.md)
- **Adding a new drone tool** → [components.md](./components.md) → `core/toolkit.py` section
- **Debugging slow responses** → [workflow.md](./workflow.md) → "Live vs text-agent latency"
- **Adding safety checks** → [safety.md](./safety.md)
- **Flying a real drone for the first time** → [safety.md](./safety.md) → "Known gaps"
- **Exploring the overall system** → [architecture.md](./architecture.md)

## Conventions used in these docs

- Code references name the file and symbol, for example `core/toolkit.py` → `TelloToolkit.operator_command`. Line numbers are deliberately avoided because they drift.
- Diagrams are [Mermaid](https://mermaid.js.org/) and render natively on GitHub and in most IDEs.
- "Live path" and "text agent" (also called the legacy path, after the `--legacy-agent` flag) distinguish the two agent implementations.
- File paths use forward slashes regardless of OS. Shell examples use Windows PowerShell.
- Anything not yet implemented or not yet validated on real hardware is flagged explicitly.

## See also

- [README.md](../README.md): quick start, operator controls, known gaps
- [prd.md](../prd.md): original product design document (not kept in sync with the code)
- [config.py](../config.py): single source of truth for models, thresholds and tool schemas
