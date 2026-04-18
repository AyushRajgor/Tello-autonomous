# Components

Module-by-module reference. For each module: what it does, key classes and functions, how to extend it, and the files it depends on.

## Layout

```
main.py
config.py
core/         — agent loop, toolkit, safety, state, tracing, cost
voice/        — Live API client + audio I/O
vision/       — standalone scene analysis (legacy path)
features/     — higher-level behaviors (object search)
dashboard/    — FastAPI web UI
scripts/      — operational utilities
tests/        — pytest suite
```

---

## `main.py`

Entry point. Parses CLI args, builds `TelloToolkit`, instantiates the tracer, picks a mode, and runs it.

| Function | File lines | Purpose |
|---|---|---|
| `create_toolkit` | `32:57` | Builds toolkit in real, sim, or sim+webcam mode. Falls back to sim if Tello connection fails. |
| `run_text_mode` | `60:63` | Legacy interactive loop (delegates to `TelloAgent.run_interactive`). |
| `run_voice_mode` | `66:153` | Live API voice loop with mic + speaker. Supports `text_only` for `--voice-text`. |
| `run_dashboard_mode` | `156:184` | Injects globals into `dashboard.server` and launches uvicorn. |
| `main` | `187:236` | Arg parsing, API key check, mode selection, graceful shutdown with auto-land. |

### Adding a new CLI mode

1. Add argparse flag in `main()` (`188:195`)
2. Add `elif args.my_flag:` branch in the dispatch block (`218:223`)
3. Implement `run_my_mode(toolkit, tracer)` somewhere above `main`

---

## `config.py`

Single source of truth for everything tunable. Imported everywhere.

| Export | Purpose | Primary consumer |
|---|---|---|
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | Secrets | agent modules |
| `MODELS` dict | Model strings keyed by role | `gemini_live.py`, `agent.py`, `llm_vision.py` |
| `THINKING` dict | `thinking_level` per task type | `agent.py`, `llm_vision.py` |
| `SAFETY` dict | All safety thresholds | `safety.py`, `toolkit.py` |
| `MOVEMENT` dict | Valid directions + scan/explore steps | `safety.py`, `features/search.py` |
| `VISION` dict | Frame size + JPEG quality | `toolkit.get_frame`, `llm_vision.py` |
| `LIVE` dict | Voice, sample rates, video FPS, disabled tools | `voice/gemini_live.py`, `voice/audio.py` |
| `DASHBOARD` dict | Host + port | `main.py`, `dashboard/server.py` |
| `TRACING` dict | Log dir, thumbnail sizing | `core/tracing.py` |
| `TOOL_SCHEMAS` list | JSON schema per drone tool | `agent.py`, `gemini_live.py` |

### Adding a new drone tool

1. Append a schema to `TOOL_SCHEMAS` (`106:218`)
2. Add a method on `TelloToolkit` that implements it
3. Add a dispatch entry in `TelloToolkit.execute` (`346:361`)
4. If it shouldn't be exposed in Live mode (because Live already handles it natively), add its name to `LIVE["disabled_tools"]` (`84`)

---

## `core/toolkit.py` — `TelloToolkit`

Wraps `djitellopy.Tello`. Every public method follows the same pattern:

1. Call the matching `SafetySystem.check_*` method
2. If not allowed, return `{success: False, error: reason}`
3. Execute the real command (or simulated no-op)
4. Update `DroneState` (dead reckoning)
5. Log the action
6. Return `{success: True, message: "..."}`

### Public surface

| Method | File lines | Notes |
|---|---|---|
| `connect` | `50:71` | Connects drone (or fake-connects simulation). Also opens webcam in `--simulate --webcam`. |
| `takeoff`, `land`, `move`, `rotate` | `75:` | Flight primitives. All validated. |
| `get_battery`, `get_height`, `get_temperature`, `get_speed`, `set_speed` | | Telemetry + speed tuning. |
| `flip` | | Requires battery >50% enforced in `SafetySystem.check_flip`. |
| `get_frame` | | Returns `{image_base64: str}`. Source is drone, webcam, or 1×1 black placeholder. |
| `emergency_stop` | `313:325` | Kills motors. Drone *will* fall. |
| `stream_on`, `stream_off` | | Drone video stream lifecycle. |
| `execute(name, args)` | `329:372` | The single entry point the agent loop uses. Dispatches to the right method by name. |

### Returning new fields

Tool results are JSON dicts. The LLM sees them as-is, so:

- Put human-readable text in `message` (or `error` on failure)
- Only include large blobs (`image_base64`) when the LLM actually needs them — they count as tokens
- Keep keys consistent across tools so the model learns the schema

---

## `core/safety.py` — `SafetySystem`

Validates actions. Never touches the drone. Returns a `SafetyCheckResult` with `allowed`, optional `reason`, and optional `modified_params` (for clamping).

| Check | File lines | Rejects or modifies |
|---|---|---|
| `check_takeoff` | `123:132` | Rejects if already flying or battery critical |
| `check_move` | `39:106` | Clamps distance, rejects on invalid direction, altitude cap, geofence |
| `check_rotate` | `108:121` | Clamps degrees to ±360, battery check |
| `check_flip` | `134:146` | Rejects if battery <50% |
| `check_battery` | `148:160` | Sets `_force_land_requested` at ≤10%, warns at ≤20% |
| `check_set_speed` | `162:167` | Clamps to [10, 100] cm/s |
| `get_status_summary` | `173:181` | Human-readable string prepended to every legacy user message |

See [safety.md](./safety.md) for the full threshold table and flight state machine.

---

## `core/state.py` — `DroneState`, `Position`

In-memory drone model. There is **no persistence** — state resets when the process dies.

| Field | Updated by | Notes |
|---|---|---|
| `position` (`Position(x,y,z)`) | `update_position_after_move` | Dead reckoning only. No IMU fusion. |
| `heading_deg` | `update_heading_after_rotate` | Yaw only. Pitch/roll not tracked. |
| `is_flying` | `takeoff`, `land`, `emergency_stop` | |
| `is_streaming` | `stream_on`, `stream_off` | |
| `battery_pct`, `height_cm`, `speed_cms` | telemetry tools | Refreshed lazily. |
| `action_log` | `log_action` called by every toolkit method | Bounded; used in traces and dashboard. |

### Why dead reckoning instead of querying the drone

The Tello reports height but not 2D position. Every known-distance move the toolkit performs is added to `x,y` based on current heading. Drift accumulates; trust the geofence as a soft boundary, not a hard guarantee.

---

## `core/tracing.py` — `TraceManager`, `Span`, `Trace`

Observability layer. One `Trace` per user message (legacy) or per Live session. Each `Trace` has many `Span`s.

| Method | File lines | Purpose |
|---|---|---|
| `start_trace(user_message, drone_state)` | | Opens a new trace, returns `trace_id` |
| `add_span(trace_id, span)` | | Appends a span to an in-flight trace |
| `end_trace(trace_id, final_response)` | | Marks the trace complete, writes to JSONL + terminal |
| `get_recent_traces(n)` | | In-memory ring buffer for the dashboard |
| `get_trace(trace_id)` | | Full trace including spans |

Output goes to:

- `logs/traces.jsonl` — one JSON object per line
- stdout (pretty-printed if `TRACING["terminal_output"]`)
- Dashboard API at `/api/traces` and `/api/traces/{id}`

See [workflow.md §6](./workflow.md#6-tracing-pipeline) for span types and producers.

---

## `core/cost.py`

Per-model pricing tables (USD per 1 M tokens). Consumed inline whenever a `llm_call` span is created.

| Model | Input | Output | Thinking |
|---|---|---|---|
| `gemini-3-flash-preview` | see `core/cost.py` | | |
| `gemini-3.1-flash-lite` | | | |
| `gemini-3.1-flash-live-preview` | 0.30 | 1.20 | 0.00 |

Update `MODEL_PRICING` (`core/cost.py`) when Google releases new pricing. The `calculate_cost(model, tokens_in, tokens_out, thinking_tokens)` function returns the dollar cost of a single call.

---

## `voice/gemini_live.py` — `LiveAgent`

Persistent Gemini Live API session. The heart of the fast path.

### Lifecycle

```python
agent = LiveAgent(toolkit, tracer=tracer)
agent.on_audio(lambda pcm: speaker.play(pcm))
agent.on_text(lambda role, text: print(f"{role}: {text}"))
agent.on_tool(lambda name, args, result: ...)
agent.on_interrupt(lambda: speaker.flush())

await agent.start()            # opens WebSocket, starts video loop
await agent.send_text("land")
await agent.send_audio(pcm)    # 16 kHz PCM chunks
await agent.stop()             # closes WebSocket
```

| Method | File lines | Purpose |
|---|---|---|
| `start` | `174:217` | Open WebSocket, launch receive + video loops |
| `stop` | `219:242` | Cancel tasks, close context, end trace |
| `send_text` | `245:253` | Push typed text into the session |
| `send_audio` | `255:277` | Push 16 kHz PCM chunk. Drops chunks while the half-duplex gate (`_model_speaking` + `_echo_guard_ms`) is active. |
| `send_video_frame` | `279:287` | Push a JPEG (used internally by video loop) |
| `_video_loop` | `290:306` | Runs at `LIVE["video_fps"]`, pulls `get_frame`, downscales, pushes to session |
| `_receive_loop` | `308:319` | Consumes server events and routes to `_handle_response` |
| `_handle_response` | `321:375` | Dispatches tool_call, audio output, transcriptions, interruptions. Sets `_model_speaking=True` on audio chunks, clears it on `turn_complete`. |
| `_run_tool_calls` | `377:440` | Executes toolkit functions via executor, traces them, returns `FunctionResponse` |

### Half-duplex echo guard

Two instance flags on `LiveAgent` guard against the speaker-to-mic feedback loop:

| Attribute | Type | Purpose |
|---|---|---|
| `_model_speaking` | `bool` | `True` while the model is actively emitting audio chunks. Cleared on `turn_complete`. |
| `_last_model_audio_ts` | `float` | Wall-clock timestamp of the most recent model audio chunk. |
| `_echo_guard_ms` | `int` (default `500`) | Grace window after the last model audio chunk during which mic input is still dropped. Covers speaker playback latency (prebuffer + OS). |

`send_audio()` drops incoming chunks when either `_model_speaking` is `True` or `time.time() - _last_model_audio_ts < _echo_guard_ms / 1000`. `interrupted` events from the server are also ignored while `_model_speaking` is `True` — those are almost always echo rather than genuine barge-in.

### Tuning for your setup

| Environment | Recommended `_echo_guard_ms` | Why |
|---|---|---|
| Headphones | `0` or very low | No acoustic path — no feedback possible |
| Built-in laptop speakers | `500` (default) | Typical speaker-to-mic lag on Windows WASAPI |
| Bluetooth speakers / headset | `800`–`1000` | BT adds 150–300 ms of its own latency |

Edit `self._echo_guard_ms` in `LiveAgent.__init__` (`voice/gemini_live.py`) or expose it as a config key in `LIVE` if you need per-run overrides.

### Callback contract

Callbacks may be sync or async — `_maybe_await` normalizes both (`411:415`). They run in the receive loop's task, so keep them short. Heavy work should be kicked onto its own task.

### Filtering tools for Live mode

`_build_live_tools()` (`61:90`) drops any tool listed in `LIVE["disabled_tools"]` before sending the declarations to Gemini. By default that's `get_frame`, `stream_on`, `stream_off` — all superseded by the in-session video stream.

---

## `voice/audio.py` — `MicStream`, `SpeakerPlayer`

Thin wrappers around `sounddevice`. Lazy imports keep the main process runnable without `sounddevice` installed.

### `MicStream`

- Opens an `InputStream` at `LIVE["input_sample_rate"]` = 16 kHz, mono, `int16`
- PortAudio callback pushes `MIC_BLOCK_MS`-sized (20 ms) chunks onto an `asyncio.Queue`-compatible queue
- Consumer in `main.py` pulls chunks and calls `agent.send_audio`

### `SpeakerPlayer`

Callback-driven playback with a jitter pre-buffer. The sound card pulls samples at a constant rate via a PortAudio callback; we only feed into a ring buffer.

| Property | Value | Purpose |
|---|---|---|
| Sample rate | `LIVE["output_sample_rate"]` = 24 kHz | What the Live API emits |
| Prebuffer | `prebuffer_ms=200` | Accumulate 200 ms of audio before playback starts. Absorbs WebSocket jitter so chunks arriving late don't create audible gaps. |
| Latency class | `"high"` | Asks PortAudio for a larger hardware buffer — big win on Windows WASAPI where default latency is tight. |
| Underrun behavior | Emit silence, reset prebuffer flag | On underrun the callback outputs zeros instead of stuttering, then re-prebuffers before resuming. |

| Method | File lines | Purpose |
|---|---|---|
| `start` | `163:191` | Open the output stream with the callback |
| `_callback` | `133:162` | Called by PortAudio — pulls `frames` samples from the buffer, pads silence on underrun |
| `play` | `193:198` | Append PCM bytes to the ring buffer |
| `flush` | `200:204` | Clear buffer and reset the prebuffer flag — used on Live API interruption |
| `stop` | `206:220` | Shut down the stream and clear the buffer |

Increase `prebuffer_ms` if you hear choppiness; decrease it if you want lower latency at the cost of some jitter tolerance. Must stay ≥ the `_echo_guard_ms` used in `LiveAgent` — otherwise the guard will lift before playback actually finishes.

### Fallback behavior

Both classes raise `RuntimeError("Audio libraries not installed: ... Install with: pip install sounddevice numpy")` on `start()` if the import failed. `main.py` catches this for the mic and falls back to text-only input. For the speaker, the dashboard just logs a warning and continues without audio playback.

---

## `vision/llm_vision.py` — `describe_scene`, `spatial_point`

Standalone vision calls used by the **legacy path only** (Live mode does perception inline).

| Function | Model used | When called |
|---|---|---|
| `describe_scene(image_b64, drone_context, trace_id)` | `gemini-3.1-flash-lite` | After `get_frame` in the legacy agent loop (`core/agent.py` `306:319`) |
| `spatial_point(image_b64, target)` | `gemini-robotics-er-1.5` | Currently unused — reserved for future object-pointing features |

Both emit `vision_call` spans when a tracer is injected via `vision.tracer = tracer` (done in `main.py` `209:211`).

---

## `features/search.py` — `ObjectSearcher`

State machine for multi-step object exploration ("go find a chair"). **Not currently wired into either agent** — present as scaffolding for Phase 4 (per `prd.md`). Exposes:

- `scan_current_room()` — 360° in 60° steps, calls `get_frame` at each
- `navigate_to_exit()` — picks the best-looking doorway
- `search_loop(target)` — repeats scan+navigate until the model finds the target

To activate it you'd expose one entry point as a tool and route the model to it.

---

## `dashboard/server.py`

FastAPI app served by uvicorn. Uses the modern `lifespan` context (`97:132`) for startup/shutdown — needed because the `live_agent.start()` coroutine has to run inside the ASGI event loop.

### Globals injected by `main.py`

```python
dashboard.server.toolkit = toolkit
dashboard.server.tracer = tracer
dashboard.server.use_live = True_or_False
dashboard.server.live_agent = LiveAgent(...)   # if use_live
dashboard.server.agent = TelloAgent(...)       # if legacy
```

### WebSocket endpoint `/ws`

Bi-directional:

- **Inbound text** — JSON messages with `type`: `command`, `audio_start`, `audio_end`, `get_frame`
- **Inbound binary** — raw 16 kHz PCM audio chunks, forwarded straight to `live_agent.send_audio`
- **Outbound** — JSON events broadcast to all connected clients: `state`, `chat`, `tool`, `trace`, `frame`, `mode`

### REST endpoints

| Route | Purpose |
|---|---|
| `/` | Serves `static/index.html` |
| `/video-status` | Whether drone stream or webcam is active |
| `/webcam-status` | Legacy alias for `/video-status` |
| `/api/traces?limit=N` | List recent traces |
| `/api/traces/{trace_id}` | Full trace with spans |

### Background tasks

- `telemetry_loop` (`282:291`) — every 2 s broadcasts `{type:"state", data: toolkit.state.to_dict()}`

---

## `dashboard/static/*`

Plain HTML + CSS + vanilla JS. No framework.

| File | Responsibility |
|---|---|
| `index.html` | Layout: video panel, chat, telemetry, trace list, mic button |
| `script.js` | WebSocket client, chat rendering, mic capture (`startMic`/`stopMic`), downsample + PCM16 encode |
| `style.css` | Layout + mic button states (idle, hover, active, recording pulse) |

The mic button uses `getUserMedia` → `AudioContext(sampleRate: 48000)` → `ScriptProcessorNode(bufferSize=4096)` → downsample to 16 kHz → `ArrayBuffer` → `ws.send(pcm)` as a binary frame. Every ~85 ms a new chunk ships.

---

## `scripts/network_check.py`

Stand-alone utility. Verifies:

1. You can reach the drone at `192.168.10.1` (default Tello AP IP)
2. You can reach the open internet (for Gemini)

Run: `python scripts/network_check.py`.

Useful when the agent hangs — it tells you whether the problem is the drone side or the internet side.

---

## `tests/`

pytest suite covering safety, state, toolkit (mocked `Tello`), and tracing.

- `tests/test_toolkit.py` — toolkit dispatch, simulation-mode side effects, state mutations
- `tests/test_tracing.py` — trace opening, span ordering, JSONL serialization

Run: `pytest`. No drone required — everything mocks `djitellopy`.

## See also

- [architecture.md](./architecture.md) — how these components fit together
- [workflow.md](./workflow.md) — runtime flows across components
- [safety.md](./safety.md) — deep dive on the safety module
