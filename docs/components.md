# Components

Module-by-module reference. For each module: what it does, key classes and functions, how to extend it, and what it depends on.

## Layout

```
main.py
config.py
core/         — toolkit, safety, watchdog, state, text agent, Gemini client, tracing, cost
voice/        — Live API client + audio I/O
vision/       — one-shot frame analysis (structured JSON)
features/     — higher-level behaviors (object search, not wired as a tool)
dashboard/    — FastAPI server + React/Vite frontend
scripts/      — operational utilities
tests/        — pytest suite
```

---

## `main.py`

Entry point. Parses CLI args, builds `TelloToolkit`, creates the tracer, starts the safety watchdog, picks a mode and runs it.

| Function | Purpose |
|---|---|
| `create_toolkit` | Simulated toolkit (optionally with webcam), or a real `Tello(retry_count=1)`. If djitellopy is missing or the drone doesn't answer, it logs an error and **exits**. There is no fallback to simulation. |
| `run_text_mode` | Delegates to `TelloAgent.run_interactive`. |
| `run_voice_mode` | Live API voice loop (open mic, `push_to_talk=False`) with mic + speaker. `--voice-text` skips the mic. Typed operator words run on their own thread via `operator_command_background`; watchdog warnings are forwarded with `send_system_notice`. |
| `run_dashboard_mode` | Injects globals into `dashboard.server` (`LiveAgent(push_to_talk=True)` by default, `TelloAgent` with `--legacy-agent`), warns if bound beyond loopback without a token, runs uvicorn. |
| `main` | Arg parsing, API key check, `stream_on` for a real drone, watchdog start, mode dispatch. The `finally` block lands via `operator_command("land")` if still flying, stops the watchdog and releases the camera. |

### Adding a new CLI mode

1. Add an argparse flag in `main()`
2. Add an `elif args.my_flag:` branch in the dispatch block
3. Implement `run_my_mode(toolkit, tracer, ...)` above `main`. If it reads input, route exact operator words through `parse_operator_text()` → `operator_command_background()`, so a typed `!estop` never waits behind a `land`.

---

## `config.py`

Single source of truth for everything tunable. Imported everywhere.

| Export | Purpose | Primary consumer |
|---|---|---|
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | Secrets from `.env` | agent, Live, vision |
| `MODELS` | `live`, `vision_reasoning`, `vision_quick`, `vision_spatial`, `voice_fallback` | `gemini_live.py`, `agent.py`, `llm_vision.py` |
| `GEMINI_HTTP_RETRY` | Retry/backoff for `generate_content` (4 attempts on 408/429/5xx) | `core/genai_client.py` |
| `THINKING` | `reasoning` = medium, `quick` = minimal, `spatial` = low | `agent.py`, `llm_vision.py` |
| `SAFETY` | Limits, battery thresholds, watchdog and keepalive timing, operator-land wait, settle/land-confirm/force-land-retry timings | `safety.py`, `toolkit.py`, `watchdog.py`, `features/search.py` |
| `SIMULATION` | Takeoff height (80 cm), battery drain (7.5 %/min) | `toolkit.py` |
| `MOVEMENT` | Valid directions, scan/explore steps | `safety.py`, `features/search.py` |
| `VISION` | Frame size, JPEG quality, media resolution | `toolkit.get_frame`, `agent.py`, `llm_vision.py` |
| `LIVE` | Voice, sample rates, video limits, VAD, turn coverage, compression, reconnect, disabled tools | `voice/gemini_live.py`, `voice/audio.py` |
| `DASHBOARD` | Host (default `127.0.0.1`), port, token, extra origins | `main.py`, `dashboard/server.py` |
| `TRACING` | Log dir/file, thumbnail sizing, terminal output | `core/tracing.py`, `llm_vision.py` |
| `TOOL_SCHEMAS` | JSON schemas for the 12 LLM tools | `toolkit.py`, `agent.py`, `gemini_live.py` |
| `OPERATOR_ONLY_ACTIONS` | `emergency_stop`, `flip`, `hover` | `toolkit.py` |

### Adding a new drone tool

1. Append a schema to `TOOL_SCHEMAS`. Never add an operator-only action: `core/toolkit.py` asserts at import that the two sets don't overlap.
2. Add a method on `TelloToolkit` that implements it (safety check → drone call through `_drone_call` / `_run_motion` → state update → result dict). For motion, do all of it inside one `with self._cmd_lock:` block, take an `epoch` argument, and catch `OperatorStopped` into an error result.
3. Add a dispatch entry in `TelloToolkit.execute` (pass the `epoch` captured there)
4. If it moves the drone, add its name to `MOTION_TOOLS` so the operator hold blocks it
5. If Live mode shouldn't expose it, add it to `LIVE["disabled_tools"]`
6. Add tests in `tests/test_safety_core.py` or `tests/test_toolkit.py`

---

## `core/toolkit.py` — `TelloToolkit`

Wraps `djitellopy.Tello`. Flight methods follow the same pattern, with steps 1–5 under `_cmd_lock` for motion:

1. Cancel with `OperatorStopped` if `stop_epoch` changed since the command was requested, then call the matching `SafetySystem.check_*` method
2. If not allowed, return `{success: False, error: reason}`; apply any clamped `modified_params`
3. Send the command through `_drone_call` / `_run_motion` (which first run `_prepare_command`: epoch check, settle wait, reply drain), or do nothing in simulation
4. Update `DroneState` (dead reckoning), then re-sync heading and altitude from the drone's sensors
5. Log the action and return `{success: True, message: "...", ...}` (or `success: False` with "interrupted" if an operator stop was sent during the motion)

### Entry points

| Entry | Who calls it | Accepts |
|---|---|---|
| `execute(name, args)` | LLMs (text agent, Live, object search) | Only `TOOL_SCHEMAS` names. Refreshes telemetry first, force-lands on critical battery, refuses `MOTION_TOOLS` during an operator hold. |
| `operator_command(action, args)` | Humans: dashboard, CLIs, spoken stop words, watchdog | `hover`, `land`, `emergency_stop`, `resume`, `takeoff`, `move`, `rotate`, `flip`. Blocking. Manual motion is single-flight ("busy" error while one runs). |
| `operator_command_background(action, args, on_done)` | CLIs, spoken stop words, watchdog force-land | Runs `operator_command` on its own daemon thread and returns immediately; `on_done(result)` runs on that thread |
| `parse_operator_text(text)` | CLIs and dashboard chat | Exact typed words: `stop`/`hover`/`!stop` → hover, `land`/`!land`, `!estop` → emergency_stop, `resume`/`!resume` |

`hover`, `land` and `emergency_stop` set `state.operator_hold` and bump `stop_epoch` (cancelling every command still waiting for the drone); `resume` and `clear_operator_hold()` clear the hold.

### Public surface

| Method | Notes |
|---|---|
| `connect` | Real: connect, read battery, set speed to 30 cm/s. Sim: battery 100 %, optional webcam. |
| `takeoff`, `land`, `move`, `rotate` | Flight primitives. Takeoff resets the origin (each takeoff is a new home) and records the IMU yaw offset; E-STOP during takeoff means not flying, and an error/interrupted takeoff reply is decided by a settled height reading. `land` is never cancelled, retries once on a lost reply, and on a real drone succeeds only once the height sensor reads ground (`land_confirm_s`). Interrupted moves/rotations return `success: false` (position may be off by up to the move length). |
| `get_battery`, `get_height`, `get_temperature`, `get_speed`, `set_speed` | Telemetry reads come from the state packet (no command sent). |
| `get_frame` | `{image_base64}` from the drone (RGB → BGR fix), the webcam, or a gray placeholder in pure simulation. Returns an error until the first real video frame arrives. |
| `stream_on`, `stream_off` | Drone video stream lifecycle. |
| `flip` (operator-only) | Needs flying and >50 % battery. |
| `hover` (operator-only) | Bumps `stop_epoch`. Sends raw `stop` without the lock **only if a motion is in flight** (an idle Tello already hovers). Aborts the move on firmware that supports `stop`. Also used by Live to abort a cancelled tool call. |
| `emergency_stop` (operator-only) | Raw `emergency`. Motors cut, the drone **falls**. |
| `_priority_land` | Used by `operator_command("land")`. Single-flight (`_land_gate`). Raw `stop` if a motion is in flight, then takes the lock (up to 25 s) and holds it through `land`, retried up to 3 times while the sensor reads airborne. If the lock never frees, a raw `land` is sent and reported as unconfirmed (`success: false`). |
| `stop_epoch` | Counter bumped by every stop/land/E-STOP and `hover()`. |
| `motion_in_flight`, `command_in_progress`, `airborne_by_sensor` | Used by `hover`/land and the watchdog's state reconciliation. |
| `mark_flying_from_sensor`, `mark_landed_from_sensor` | Watchdog resync of `is_flying`. |
| `refresh_telemetry`, `send_keepalive`, `seconds_since_state_packet`, `simulate_tick` | Used by the watchdog. |

### Real-drone hardening

- **One re-entrant command lock** (`_cmd_lock`, a `threading.RLock`): agent threads, the keepalive and the dashboard never interleave UDP commands. Check + send + state update for every motion run under it, so concurrent moves can't breach the geofence/altitude limits; priority land re-enters it to hold it across stop → land.
- **Raw stop/land/E-STOP** (`_send_immediate`) skip the lock and set a settle window. The next command's `_prepare_command` waits until `abort_settle_s` past the interrupted motion (cancellable by another STOP, feeds `rc 0 0 0 0` keepalives, ends early after `stationary_confirm_s` of zero velocity and steady yaw), then drains the reply queue. djitellopy hands out replies in arrival order without matching them, so a late reply would otherwise be read as the next command's.
- **Stop epoch**: `execute()` and manual `operator_command` capture `stop_epoch`; a command whose epoch is stale when it gets the lock (or during the settle wait) raises `OperatorStopped` and is returned as an error, not flown.
- **Single-flight gates**: `_manual_gate` (manual motion) and `_land_gate` (priority land) refuse a second request instead of queuing it.
- **No double moves**: the Tello replies only after a move finishes, and djitellopy re-sends on timeout. The toolkit uses `Tello(retry_count=1)` and a timeout sized to the motion (`expected_s * 1.5 + 3`).
- **Sensor sync**: after each command, altitude comes from `get_height()` and heading from IMU yaw relative to the takeoff yaw. The yaw sign is learned from the first rotation of 20° or more.
- **Link-loss detection**: djitellopy replaces its state dict on every packet, so `seconds_since_state_packet()` compares object identity.

### Returning new fields

Tool results are JSON dicts that the LLM reads directly:

- Put human-readable text in `message` (or `error` on failure)
- Keep large blobs out of the dict. The text agent strips `image_base64` and sends the JPEG as a function-response part; the Live agent replaces it with a note and sends the frame to the video input
- Keep keys consistent across tools so the model learns the schema

---

## `core/safety.py` — `SafetySystem`

Validates single commands. Never touches the drone. Returns a `SafetyCheckResult` with `allowed`, optional `reason`, and optional `modified_params` (clamped values).

| Check | Rejects or modifies |
|---|---|
| `check_takeoff` | Rejects if already flying, or battery ≤20 % |
| `check_move` | Rejects bad direction, not flying, critical battery. Clamps distance to 20–100 cm, clamps up/down to the 200 cm ceiling / 20 cm floor (rejects if less than 20 cm of room), rejects if the projected position leaves the 800 cm geofence |
| `check_rotate` | Rejects if not flying or critical battery; clamps to ±360° |
| `check_flip` | Rejects bad direction, not flying, or battery ≤50 % |
| `check_battery` | Rejects at ≤10 %; allows with a `WARNING:` reason at ≤20 % |
| `check_set_speed` | Clamps to 10–100 cm/s |
| `should_force_land` (property) | True while flying at ≤10 % |
| `get_status_summary` | Status line prepended to every text-agent message (includes `OPERATOR HOLD` / `LINK LOST`) |

See [safety.md](./safety.md) for the full threshold table.

---

## `core/watchdog.py` — `SafetyWatchdog`

Daemon thread, one `tick()` per `SAFETY["watchdog_interval_s"]` (1 s). A thread rather than a task so it behaves the same in the blocking text REPL, voice mode and the dashboard.

| Each tick | Behaviour |
|---|---|
| `simulate_tick(dt)` | Simulation only: drains the battery at 7.5 %/min while flying |
| `refresh_telemetry()` | Battery, height and heading from the state packet |
| Link check | No state packet for >5 s → `state.link_ok = False`, one `critical` `link_lost` event; `info` `link_restored` when packets return |
| State reconciliation | Real drone: the height sensor disagrees with `is_flying` for 3 consecutive ticks while no command holds the lock and no motion is in flight → `mark_flying_from_sensor()` (`critical` `state_resync_airborne`; keepalive and battery protection resume) or `mark_landed_from_sensor()` (`warning` `state_resync_landed`) |
| Battery check | ≤20 % → one `warning` `battery_low` event per crossing; flying and ≤10 % → one `critical` `battery_critical` event per episode and `operator_command_background("land")` on its own thread; a failure emits `force_land_failed` once and is retried every 5 s |
| Keepalive | Flying, link OK, ≥8 s since the last command → `send_keepalive()` (`rc 0 0 0 0`, skipped if a command holds the lock) |

Listeners (`add_listener(cb)`) receive `{"level", "code", "message"}` **on the watchdog thread**. `main.py` prints them in text/voice modes and forwards warnings to the Live model; the dashboard bridges them to the event loop and broadcasts `{"type": "safety", ...}`.

---

## `core/state.py` — `DroneState`, `Position`

In-memory drone model. There is **no persistence**: state resets when the process dies.

| Field | Updated by | Notes |
|---|---|---|
| `position` (`Position(x, y, z)`) | `update_position_after_move`, `reset_origin` on takeoff | x/y dead-reckoned via `project_move()`; z re-synced from the height sensor on a real drone |
| `heading_deg` | `update_heading_after_rotate`, toolkit sensor sync | 0° = facing at takeoff, clockwise positive |
| `is_flying`, `is_streaming` | toolkit | |
| `battery_pct`, `height_cm`, `speed_cms`, `temperature` | toolkit, watchdog | |
| `link_ok` | watchdog | False after 5 s without state packets |
| `operator_hold` | `operator_command`, `clear_operator_hold` | Blocks AI motion |
| `action_log` | `log_action` | Bounded to 1000 entries; `source` says who acted (`ai`, `operator`, `watchdog`, `safety`, `voice_stop_word`, `voice_interrupt`). Interrupted moves/rotations are logged with `interrupted: true`; watchdog resyncs as `resync_airborne` / `resync_landed` |

`project_move(direction, cm, heading)` is shared by dead reckoning and the geofence projection so the two can't disagree.

### Why dead reckoning

The Tello reports height and attitude but not 2D position. Each move is added to x/y using the current heading. Drift accumulates; treat the geofence as a soft boundary, not a hard guarantee.

---

## `core/agent.py` — `TelloAgent`

The text agent (default text mode and `--dashboard --legacy-agent`), on `MODELS["vision_reasoning"]` (`gemini-3.8-flash`, thinking `medium`, `max_output_tokens` 4096, no temperature).

| Piece | Behaviour |
|---|---|
| `process_message(text, release_hold=True)` | Records `stop_epoch` on arrival, then waits for the `asyncio.Lock`. When the turn starts it clears the operator hold (new instruction), unless `release_hold=False` (Observe) or a STOP happened after the message was sent, in which case the model is told not to move. Prepends `[Drone Status: ...]`, runs the turn, then compacts history. |
| `_run_turn` | Up to `MAX_ITERATIONS` (15) model calls. Model turns are appended verbatim (they carry thought signatures); all function responses of a step go in one user turn with matching `id`/`name`. |
| `_execute_tool` | `asyncio.to_thread(toolkit.execute, ...)`. For `get_frame` the JPEG is attached as a `FunctionResponsePart`, so the planner sees the image directly. |
| History | After each turn, images in function responses are replaced by a short note. Only the last 10 user turns are kept, cut at user-text boundaries. On an error or cancellation the turn is rolled back. |
| `run_interactive` | REPL with a background input thread: `stop` / `land` / `resume` / `!estop` run immediately via `operator_command_background` (one thread each, so `!estop` never waits behind `land`), even mid-request; `status`, `reset`, `quit` (waits for operator threads, lands first). |

---

## `core/genai_client.py`

`make_client()` builds the `genai.Client` for the HTTP paths (text agent and vision) with `HttpRetryOptions` from `config.GEMINI_HTTP_RETRY`, so transient 429/5xx errors are retried with backoff. Returns `None` without an API key. The Live session builds its own client and has its own reconnect logic.

---

## `core/tracing.py` — `TraceManager`, `Span`, `Trace`

One `Trace` per text-agent message, or one per Live session. Each `Trace` has many `Span`s.

| Method | Purpose |
|---|---|
| `start_trace(user_message, drone_state)` | Opens a trace, returns `trace_id` |
| `add_span(trace_id, span)` | Appends a span to an open trace |
| `end_trace(trace_id, final_response)` | Computes totals, writes JSONL, prints a summary |
| `get_recent_traces(n)` | Completed traces, newest first (dashboard) |
| `get_trace(trace_id)` | One completed trace with spans |

Output goes to `logs/traces.jsonl` (one trace per line), the terminal (if `TRACING["terminal_output"]`), and `/api/traces`. Only **completed** traces are listed, so a Live session's trace appears when the session stops.

---

## `core/cost.py`

`MODEL_PRICING` (USD per 1M tokens, October 2026; output rates include thinking) and `calculate_cost(model, tokens_in, tokens_out, thinking_tokens)`.

| Model | Input | Output | Thinking |
|---|---|---|---|
| `gemini-3.8-flash` | 0.75 | 3.75 | 3.75 |
| `gemini-3.5-flash-lite` | 0.30 | 2.50 | 2.50 |
| `gemini-3.8-live` (text rates) | 0.75 | 4.50 | 0.00 |
| `gemini-robotics-er-2-preview` | 1.00 | 5.00 | 5.00 |

Live audio is billed at $3.00/M in and $12.00/M out, and image/video input at $1.00/M; the table only holds text rates. Scheduled increases on 2027-01-01 (not yet applied): `gemini-3.8-flash` to $1.50/$7.50, Robotics-ER 2 to $2.00/$10.00. Unknown models fall back to $0.50/$1.50.

---

## `voice/gemini_live.py` — `LiveAgent`

Persistent Gemini Live session (`gemini-3.8-live`) that hears the user, sees the drone camera and flies the drone through `toolkit.execute`.

### Lifecycle

```python
agent = LiveAgent(toolkit, tracer=tracer, push_to_talk=True)   # dashboard; False for CLI open mic
agent.on_audio(speaker.play)          # cb(pcm_bytes)
agent.on_text(on_text)                # cb(role, text); role: user | assistant | system
agent.on_tool(on_tool)                # cb(name, args, result)
agent.on_interrupt(speaker.flush)     # cb()
agent.on_metric(on_metric)            # cb(name, value), e.g. "voice_latency_ms"

await agent.start()                   # opens the first connection (raises on failure)
await agent.begin_user_turn()         # push-to-talk pressed
await agent.send_audio(pcm)           # 16 kHz PCM16
await agent.end_user_turn()           # push-to-talk released
await agent.send_text("take off")
await agent.send_system_notice("Battery low (19%)")
await agent.stop()
```

### Session config (`build_live_config`)

- Audio responses with the Aoede voice; input and output transcription on.
- Tools from `TOOL_SCHEMAS` minus `LIVE["disabled_tools"]` (`get_frame`, `stream_on`, `stream_off`), every one declared **`BLOCKING`** (3.8 Live defaults to non-blocking).
- No `thinking_config`, temperature, affective dialog or proactivity settings (unsupported or errors on 3.8).
- `media_resolution` LOW. Context-window compression (trigger 25,600 tokens, target 12,800) lifts the 2-minute audio+video session cap.
- Session resumption handle on every connect.
- Push-to-talk: automatic activity detection disabled, turn coverage `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO`. Open mic: server VAD (low start sensitivity, 500 ms silence, 200 ms prefix), turn coverage `TURN_INCLUDES_ONLY_ACTIVITY`.

### Behaviour

| Area | What happens |
|---|---|
| Connection | `_supervise` runs the receive loop. `go_away` → finish in-flight tools, wait for a resumable handle, reconnect with it. Dropped connection → exponential backoff (1 s doubling, 5 attempts). A handle that fails twice is dropped and a fresh session starts. After giving up, `link_failed` is True and the chat says "restart required". Tool calls of a dropped connection (`_orphan_tool_calls`) are cancelled if not yet started; running ones finish, but their responses never go to the new connection. One summary notice (what ran, what was cancelled, current status) goes to the model after reconnect. |
| Tool calls | Each call is its own task keyed by call id, serialized by `_tool_lock`, executed with `asyncio.to_thread(toolkit.execute, ...)`. The response carries the result plus a compact drone-state summary. After takeoff/land/move/rotate a fresh frame is sent before the response. |
| Cancellation | `tool_call_cancellation` cancels queued calls; a running takeoff/move/rotate is aborted with `toolkit.hover()`, which bumps `stop_epoch` so toolkit commands still waiting are cancelled too. |
| Stop words | A whole utterance like "stop", "hover", "freeze", "land now" (fillers such as "please" ignored) runs `operator_command` immediately on a dedicated thread (`operator_command_background`, not the shared executor), before any pending tool call. |
| Operator hold | `send_text(text, release_hold=True)` clears it only if the message can be delivered (kept while the link is down, restored if the send fails); `release_hold=False` keeps it. `begin_user_turn` clears it. In open-mic mode, a new non-stop-word utterance clears it. `send_system_notice` keeps it. |
| Video | JPEG, max 640 px, quality 60, never more than 1 FPS. Open mic streams continuously; push-to-talk only sends frames while the button is held, while tools run, and right after a move. |
| Echo gate (open mic only) | Mic audio is dropped while the model speaks and for 500 ms after its last audio chunk. Push-to-talk never gates the mic, so holding the button barges in. Server `interrupted` events always flush playback. |
| Latency | `voice_latency_ms` metric from end of user input to the first model audio or transcript. |

### Callback contract

Callbacks may be sync or async; `_maybe_await` normalizes both. They run on the event loop inside the receive path, so keep them short.

---

## `voice/audio.py` — `MicStream`, `SpeakerPlayer`

Thin wrappers around `sounddevice`. Imports are guarded so `main.py` runs without audio libraries.

- **`MicStream`**: 16 kHz mono `int16`, 20 ms blocks pushed onto an `asyncio.Queue`. Used by `--voice`.
- **`SpeakerPlayer`**: 24 kHz mono `int16` playback through a PortAudio callback and ring buffer. Waits for `prebuffer_ms` (200 ms) before playing, emits silence on underrun and re-prebuffers, uses `latency="high"`. `flush()` clears the buffer on interruption.

Both raise `RuntimeError` on `start()` if the audio libraries failed to import. `main.py` falls back to typed input if the mic fails; the dashboard continues without host audio if the speaker fails.

---

## `vision/llm_vision.py`

One-shot frame analysis. **The text agent no longer uses it** (frames go straight to the planner), and the Live path sees video directly. It serves `features/search.py` and any caller that needs a single analysis. All calls emit `vision_call` spans when a tracer is injected (`vision.tracer = tracer` in `main.py`).

| Function | Model | Output |
|---|---|---|
| `analyze_frame(image_b64, prompt, mode, context, trace_id, response_schema)` | `quick` → `gemini-3.5-flash-lite` (thinking minimal); `reasoning` → `gemini-3.8-flash` (thinking medium) | Text, or raw JSON when a schema is given |
| `describe_scene` | reasoning | Plain text |
| `check_path_clear` | quick | `{clear, obstacle, distance_estimate}`; anything unparseable counts as **not clear** |
| `search_for_object` | reasoning | `{found, location_in_frame, confidence, description, suggestion}`; unparseable → not found |
| `find_doors_and_openings` | reasoning | `{openings[], room_type, navigable}`; invalid fields become `unknown` |

Structured answers use `response_mime_type="application/json"` plus `response_json_schema`; a tolerant parser and safe defaults remain as a safety net. `MODELS["vision_spatial"]` (`gemini-robotics-er-2-preview`) is configured but no function uses it.

---

## `features/search.py` — `ObjectSearcher`

Multi-room object search ("go find a chair"). **Not exposed as a tool and not called by either agent.**

- `scan_room(target)`: six 60° steps, `search_for_object` on each frame
- `find_exit()`: rotate until `find_doors_and_openings` reports an open, medium/wide opening
- `align_to_opening(opening)`: turn ±30° toward an opening seen left/right
- `navigate_through_opening()`: up to four 80 cm forward steps, `check_path_clear` before each
- `search(target, max_rooms=3)`: scan → exit → align → navigate, per room

Every drone action goes through `toolkit.execute()` in a worker thread, so clamps, force-land and the operator hold apply. The search aborts (`{"found": False, "aborted": True, "reason": ...}`) on operator hold, not flying, battery ≤20 %, or any tool error. To activate it, expose one entry point as a tool and route the model to it.

---

## `dashboard/server.py`

FastAPI app served by uvicorn. The `lifespan` context starts the host speaker and the Live session, bridges watchdog events to the loop, and runs the telemetry loop.

### Globals injected by `main.py`

```python
dashboard.server.toolkit = toolkit
dashboard.server.tracer = tracer
dashboard.server.watchdog = watchdog
dashboard.server.use_live = True_or_False
dashboard.server.live_agent = LiveAgent(toolkit, tracer=tracer, push_to_talk=True)   # if use_live
dashboard.server.agent = TelloAgent(toolkit, tracer)                                  # if --legacy-agent
```

### Access control

- Binds `127.0.0.1` by default. `main.py` warns if `DASHBOARD_HOST` is not loopback and no token is set.
- **Origin allowlist** on `/ws` (Cross-Site WebSocket Hijacking protection): localhost/127.0.0.1/[::1] on the dashboard port, the Vite dev origins, `DASHBOARD_ALLOWED_ORIGINS`, and the bound host. When bound beyond loopback, an IP-literal Origin that equals the Host header is also accepted. A missing Origin (non-browser client) is allowed.
- **Optional `DASHBOARD_TOKEN`**: `/ws` and `/api/*` require `?token=`. The frontend copies `?token=` from the page URL. `/`, `/static`, `/video-status` and `/webcam-status` are not token-protected.

### WebSocket `/ws`

| Browser → server | Effect |
|---|---|
| `{"type":"command","text":...,"keep_hold":false}` | Exact operator words (`stop`, `land`, `resume`, `!estop`, ...) run as operator actions; anything else goes to `LiveAgent.send_text` or `TelloAgent.process_message` with `release_hold = not keep_hold`. The server never clears the hold itself; the agent does when the turn starts. Observe sends `keep_hold: true`. |
| `{"type":"operator","action":...,"args":{...}}` | Direct `operator_command` (hover, land, emergency_stop, resume, takeoff, move, rotate, flip), never queued behind the AI. hover/emergency_stop/resume run on `_INSTANT_POOL`, land on `_LAND_POOL`, so STOP/E-STOP can't be starved by Land presses or other work; manual motion uses the default executor. |
| `{"type":"audio_start"}` / `{"type":"audio_end"}` | Push-to-talk: `begin_user_turn` (which lifts the hold) / `end_user_turn` |
| binary frame | PCM16 mic audio → `send_audio` (per-connection ordered queue, capped at 500 chunks) |
| `{"type":"get_frame"}` | Replies with one `frame` |

Server → browser: `state`, `mode`, `chat`, `tool`, `operator_result`, `safety`, `trace`, `metric`, `frame`.

### HTTP routes

| Route | Purpose |
|---|---|
| `/` | `static_dist/index.html`, or HTTP 503 with build instructions if missing (checked per request) |
| `/static/*` | Built assets (404 with instructions while the build is missing) |
| `/video-status` | Whether the drone stream or webcam is active |
| `/webcam-status` | Legacy alias for `/video-status` |
| `/api/traces?limit=N`, `/api/traces/{trace_id}` | Completed traces |

### Background tasks

- `telemetry_loop`: every 1 s broadcasts `{type: "state"}` (no drone I/O; the watchdog refreshes telemetry)

---

## `dashboard/frontend/`

React 19 + Vite + Tailwind console, built to `dashboard/static_dist` (served under `/static/`).

| Area | Files | Notes |
|---|---|---|
| Shell, socket, settings | `src/App.tsx` | Reconnecting WebSocket, header badges (connection, mode, low battery, LINK LOST, AI paused), settings (camera, voice, latency, FPS) |
| Safety controls | `src/features/controls/FlightControls.tsx` → `EmergencyControls` | STOP (Space), Land (Esc), E-STOP with a 5 s confirm window |
| Manual flight | `FlightControls` | Takeoff, 50 cm moves, 90° turns, flip forward (operator actions, single-flight in the toolkit); "Observe" asks the AI with `keep_hold: true`, so it never lifts a STOP |
| Alerts and banners | `src/features/safety/SafetyAlerts.tsx`, `SafetyBanners.tsx` | Toasts for `safety` events (critical stays until dismissed); LINK LOST banner; "AI paused by operator" banner with **Resume AI** |
| Hotkeys | `src/lib/hotkeys.ts` | Space → STOP, Esc → Land; capture phase; ignored while typing or inside overlays |
| Push-to-talk | `src/lib/audio.ts` | `ScriptProcessorNode` (4096 samples) → 16 kHz PCM16 → binary frames |
| Video | `src/lib/video-stream.ts` | Requests `get_frame` over the socket (target 30 FPS, one request in flight) |
| Token | `src/lib/api.ts` | Copies `?token=` from the page URL to `/ws` and `/api` requests |

---

## `scripts/network_check.py`

Verifies that the drone answers `command` at `192.168.10.1:8889` and that `generativelanguage.googleapis.com:443` is reachable.

Run: `.\venv\Scripts\python.exe scripts\network_check.py`. Useful when the agent hangs: it tells you whether the drone side or the internet side is the problem.

---

## `tests/`

233 pytest tests. Everything runs in simulation, against a `FakeTello`, or with fake Gemini clients; no API key or drone needed.

| File | Covers |
|---|---|
| `test_safety_core.py` | Tool surface vs operator-only actions, enforced limits, operator hold, watchdog (force-land retry, state reconciliation), real-drone paths with `FakeTello`: stop epoch, settle window, land/takeoff sensor confirmation, single-flight land and manual motion, concurrent-move geofence |
| `test_toolkit.py` | Dispatch, simulation side effects, geofence, dead reckoning |
| `test_agent.py` | Text agent: off-loop tools, serialization, multimodal frames, history trimming and rollback, REPL operator commands |
| `test_live_agent.py` | Live config, push-to-talk turns, tool tasks and cancellation, reconnects, stop words |
| `test_live_latency.py` | `voice_latency_ms` metric |
| `test_server.py` | Static/503 handling, Origin and token checks, operator messages, push-to-talk, watchdog bridge |
| `test_vision.py` | Structured output, safe defaults, spans |
| `test_search.py` | Object search uses `execute()` off the loop and aborts safely |
| `test_model_config.py` | Current model IDs, pricing entries, SDK pins, HTTP retry |
| `test_tracing.py` | Trace lifecycle and serialization |

Run: `.\venv\Scripts\python.exe -m pytest tests/ -q`. Frontend: `cd dashboard\frontend; npm test` (38 Vitest tests).

## See also

- [architecture.md](./architecture.md): how these components fit together
- [workflow.md](./workflow.md): runtime flows across components
- [safety.md](./safety.md): deep dive on the safety layer
