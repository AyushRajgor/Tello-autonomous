import { Monitor, Moon, Settings, Sun } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Composer } from "@/features/chat/Composer";
import { EmergencyControls, FlightControls, QuickSafetyBar } from "@/features/controls/FlightControls";
import { MissionLog } from "@/features/log/MissionLog";
import { FlightData } from "@/features/map/FlightData";
import { PreflightList, VerdictBadge, preflightChecks, preflightVerdict } from "@/features/safety/PreflightPanel";
import { SafetyAlerts } from "@/features/safety/SafetyAlerts";
import { SafetyBanners } from "@/features/safety/SafetyBanners";
import { Panel } from "@/features/shell/Panel";
import { TopBar } from "@/features/shell/TopBar";
import { SystemPanels } from "@/features/telemetry/SystemPanels";
import { TracePanel } from "@/features/traces/TracePanel";
import { VideoStage } from "@/features/video/VideoStage";
import { fetchTraces, fetchVideoStatus, getDashboardToken, sendJson, sendOperator } from "@/lib/api";
import { PushToTalkAudio } from "@/lib/audio";
import {
  createDashboardModel,
  describeOperatorAction,
  dismissAlert,
  isLinkLost,
  isOperatorHold,
  mergeFetchedTraces,
  pushAlert,
  reduceDashboardMessage,
  type DashboardMessage
} from "@/lib/dashboard-state";
import { buildWebSocketUrl, getPositionParts, getStateMetric } from "@/lib/format";
import { useOperatorHotkeys } from "@/lib/hotkeys";
import { appendTrail, type TrailPoint } from "@/lib/trail";
import { createFrameRequestLoop, type FrameRequestLoop } from "@/lib/video-stream";
import type { ConnectionState, DashboardSnapshot, OperatorAction, OperatorArgs, VideoStatus } from "@/types";

type Theme = "system" | "light" | "dark";

type Settings = {
  theme: Theme;
  camera: boolean;
  voice: boolean;
  showLatency: boolean;
  showFps: boolean;
};

const DEFAULT_SETTINGS: Settings = { theme: "system", camera: true, voice: true, showLatency: true, showFps: true };
const SETTINGS_KEY = "telloai.settings";
/** Give up on the "AI working" indicator if no reply ever arrives. */
const AWAIT_REPLY_TIMEOUT_MS = 45_000;

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) {
      return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) };
    }
  } catch {
    // ignore storage failures
  }
  return DEFAULT_SETTINGS;
}

export function App() {
  const [model, setModel] = React.useState(createDashboardModel);
  const [connection, setConnection] = React.useState<ConnectionState>("connecting");
  const [videoStatus, setVideoStatus] = React.useState<VideoStatus>({ active: false });
  const [micActive, setMicActive] = React.useState(false);
  const [settings, setSettings] = React.useState<Settings>(loadSettings);
  const [fps, setFps] = React.useState(0);
  const [latencyMs, setLatencyMs] = React.useState<number | null>(null);
  const [awaitingSince, setAwaitingSince] = React.useState<number | null>(null);
  const [trail, setTrail] = React.useState<TrailPoint[]>([{ x: 0, y: 0 }]);
  const [draft, setDraft] = React.useState("");
  const composerInputRef = React.useRef<HTMLInputElement | null>(null);
  const socketRef = React.useRef<WebSocket | null>(null);
  const audioRef = React.useRef<PushToTalkAudio | null>(null);
  const frameLoopRef = React.useRef<FrameRequestLoop | null>(null);
  const micActiveRef = React.useRef(false);
  const frameCountRef = React.useRef(0);

  const setSetting = React.useCallback(<K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSettings((current) => ({ ...current, [key]: value }));
  }, []);

  React.useEffect(() => {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch {
      // ignore storage failures
    }
  }, [settings]);

  useTheme(settings.theme);

  const appendSystemMessage = React.useCallback((text: string) => {
    setModel((current) => reduceDashboardMessage(current, { type: "chat", role: "system", text }));
  }, []);

  React.useEffect(() => {
    if (typeof window === "undefined" || !("WebSocket" in window)) {
      setConnection("disconnected");
      return;
    }

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      setConnection("connecting");
      const socket = new WebSocket(
        buildWebSocketUrl(window.location.protocol, window.location.host, getDashboardToken())
      );
      socketRef.current = socket;

      socket.onopen = () => {
        if (!cancelled) {
          setConnection("connected");
        }
      };

      socket.onmessage = (event) => {
        if (typeof event.data !== "string") {
          return;
        }
        let payload: DashboardMessage;
        try {
          payload = JSON.parse(event.data) as DashboardMessage;
        } catch {
          appendSystemMessage("Dashboard received an unreadable socket payload.");
          return;
        }

        if (payload.type === "frame") {
          frameCountRef.current += 1;
          frameLoopRef.current?.markFrameReceived();
        }
        if (payload.type === "metric") {
          const metric = payload as { name?: string; value?: number };
          if (metric.name === "voice_latency_ms") {
            const value = Number(metric.value);
            if (Number.isFinite(value)) {
              setLatencyMs(value);
            }
          }
          return;
        }
        if (payload.type === "chat") {
          const role = (payload as { role?: string }).role;
          if (role === "assistant" || role === "system") {
            setAwaitingSince(null);
          }
        }
        setModel((current) => reduceDashboardMessage(current, payload));
      };

      socket.onerror = () => {
        if (!cancelled) {
          setConnection("disconnected");
        }
      };

      socket.onclose = () => {
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        if (!cancelled) {
          setConnection("disconnected");
          reconnectTimer = setTimeout(connect, 1800);
        }
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [appendSystemMessage]);

  React.useEffect(() => {
    if (typeof fetch !== "function") {
      return;
    }

    const controller = new AbortController();

    async function refresh() {
      try {
        setVideoStatus(await fetchVideoStatus(controller.signal));
      } catch {
        if (!controller.signal.aborted) {
          setVideoStatus({ active: false });
        }
      }
    }

    refresh();
    const timer = setInterval(refresh, 2500);

    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  React.useEffect(() => {
    if (typeof fetch !== "function") {
      return;
    }

    const controller = new AbortController();

    async function refresh() {
      try {
        const traces = await fetchTraces(50, controller.signal);
        setModel((current) => mergeFetchedTraces(current, traces));
      } catch {
        // Traces are optional; keep the dashboard quiet when tracing is disabled.
      }
    }

    refresh();
    const timer = setInterval(refresh, 5000);

    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  // Request video frames only while connected, streaming, and camera is enabled.
  React.useEffect(() => {
    if (connection !== "connected" || !videoStatus.active || !settings.camera) {
      return;
    }

    // 30 fps target with one in-flight request to avoid stale frame queues.
    const loop = createFrameRequestLoop({
      sendFrameRequest: () => sendJson(socketRef.current, { type: "get_frame" }),
      isVisible: () => document.visibilityState !== "hidden"
    });
    frameLoopRef.current = loop;

    return () => {
      loop.stop();
      if (frameLoopRef.current === loop) {
        frameLoopRef.current = null;
      }
    };
  }, [connection, videoStatus.active, settings.camera]);

  // Frames-per-second meter (counts frame payloads over a 1s window).
  React.useEffect(() => {
    const timer = setInterval(() => {
      setFps(frameCountRef.current);
      frameCountRef.current = 0;
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  React.useEffect(() => {
    if (awaitingSince === null) {
      return;
    }
    const timer = setTimeout(() => setAwaitingSince(null), AWAIT_REPLY_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [awaitingSince]);

  const position = getPositionParts(model.state);
  const isFlying = getStateMetric<boolean>(model.state, "is_flying", false);

  // Grow the flight trail from the dead-reckoned position the server pushes.
  React.useEffect(() => {
    setTrail((current) => appendTrail(current, { x: position.x, y: position.y }, isFlying));
  }, [position.x, position.y, isFlying]);

  // keepHold: camera-only requests (Observe) must not lift an operator STOP.
  const sendCommand = React.useCallback(
    (text: string, keepHold = false) => {
      setLatencyMs(null);
      const payload = keepHold ? { type: "command", text, keep_hold: true } : { type: "command", text };
      const sent = sendJson(socketRef.current, payload);
      if (!sent) {
        appendSystemMessage("Command not sent — the dashboard socket is offline.");
        return;
      }
      setAwaitingSince(Date.now());
    },
    [appendSystemMessage]
  );

  // Direct operator control — bypasses the AI entirely.
  const sendOperatorAction = React.useCallback((action: OperatorAction, args?: OperatorArgs) => {
    if (sendOperator(socketRef.current, action, args)) {
      return;
    }
    const label = describeOperatorAction(action, args as Record<string, unknown> | undefined);
    const safetyCritical = action === "hover" || action === "land" || action === "emergency_stop";
    setModel((current) =>
      pushAlert(
        reduceDashboardMessage(current, {
          type: "chat",
          role: "system",
          text: `${label} not sent — the dashboard socket is offline.`
        }),
        {
          level: safetyCritical ? "critical" : "warning",
          code: "socket_offline",
          message: `${label} NOT sent — the dashboard is offline and cannot reach the drone.`
        }
      )
    );
  }, []);

  const handleStop = React.useCallback(() => sendOperatorAction("hover"), [sendOperatorAction]);
  const handleLand = React.useCallback(() => sendOperatorAction("land"), [sendOperatorAction]);
  const handleResume = React.useCallback(() => sendOperatorAction("resume"), [sendOperatorAction]);
  const handleDismissAlert = React.useCallback((id: string) => {
    setModel((current) => dismissAlert(current, id));
  }, []);

  useOperatorHotkeys({ onStop: handleStop, onLand: handleLand });

  const startMic = React.useCallback(async () => {
    if (micActiveRef.current) {
      return;
    }
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      appendSystemMessage("Microphone unavailable while the dashboard socket is offline.");
      return;
    }

    try {
      const audio = audioRef.current ?? new PushToTalkAudio();
      audioRef.current = audio;
      setLatencyMs(null);
      sendJson(socketRef.current, { type: "audio_start" });
      await audio.start((frame) => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(frame);
        }
      });
      micActiveRef.current = true;
      setMicActive(true);
    } catch (error) {
      appendSystemMessage(error instanceof Error ? error.message : "Could not start microphone.");
      micActiveRef.current = false;
      setMicActive(false);
    }
  }, [appendSystemMessage]);

  const stopMic = React.useCallback(async () => {
    if (!micActiveRef.current) {
      return;
    }

    micActiveRef.current = false;
    setMicActive(false);
    if (sendJson(socketRef.current, { type: "audio_end" })) {
      setAwaitingSince(Date.now());
    }
    await audioRef.current?.stop();
  }, []);

  React.useEffect(() => {
    return () => {
      void audioRef.current?.stop();
    };
  }, []);

  const snapshot = React.useMemo<DashboardSnapshot>(
    () => ({
      mode: model.mode,
      simulated: model.simulated,
      connection,
      state: model.state,
      videoStatus,
      tools: model.tools,
      traces: model.traces
    }),
    [connection, model.mode, model.simulated, model.state, model.tools, model.traces, videoStatus]
  );

  const battery = getStateMetric<number | undefined>(model.state, "battery_pct", undefined);
  const heading = getStateMetric<number | undefined>(model.state, "heading_deg", undefined);
  const height = getStateMetric<number | undefined>(model.state, "height_cm", isFlying ? position.z : undefined);
  const speed = getStateMetric<number | undefined>(model.state, "speed_cms", undefined);
  const flightSeconds = getStateMetric<number | undefined>(model.state, "flight_duration_s", undefined);
  const connected = connection === "connected";
  const linkLost = isLinkLost(model.state);
  const operatorHold = isOperatorHold(model.state);
  const checks = preflightChecks({
    connected,
    simulated: model.simulated,
    linkLost,
    battery,
    video: videoStatus.active && settings.camera,
    mode: model.mode,
    operatorHold
  });

  const suggest = React.useCallback((text: string) => {
    setDraft(text);
    composerInputRef.current?.focus();
  }, []);

  return (
    <div className="flex min-h-dvh flex-col xl:h-dvh xl:overflow-hidden">
      <a
        href="#camera"
        className="sr-only rounded-md bg-card px-3 py-2 text-sm font-medium shadow-overlay focus:not-sr-only focus:fixed focus:start-4 focus:top-4 focus:z-50"
      >
        Skip to camera
      </a>
      <TopBar
        connection={connection}
        simulated={model.simulated}
        mode={model.mode}
        flying={isFlying}
        linkLost={linkLost}
        operatorHold={operatorHold}
        battery={battery}
        flightSeconds={flightSeconds}
        settings={<SettingsMenu settings={settings} onChange={setSetting} />}
      />

      {/* DOM order is reading order: camera and flight data, conversation, then controls. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 p-4 pb-28 lg:grid-cols-[minmax(0,1fr)_340px] lg:pb-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,380px)_340px]">
        <main id="camera" tabIndex={-1} className="flex min-h-0 min-w-0 flex-col gap-4 outline-none">
          <SafetyBanners operatorHold={operatorHold} onResume={handleResume} />
          <VideoStage
            frameSrc={model.frameSrc}
            videoStatus={videoStatus}
            cameraOn={settings.camera}
            fps={settings.showFps ? fps : null}
            latencyMs={settings.showLatency ? latencyMs : null}
            linkLost={linkLost}
          />
          <FlightData x={position.x} y={position.y} heading={heading} height={height} speed={speed} trail={trail} />
        </main>

        <section
          aria-label="Conversation"
          className="surface flex min-h-0 flex-col overflow-hidden max-xl:h-[560px] lg:col-start-1 xl:col-start-2 xl:row-start-1"
        >
          <Tabs defaultValue="log" className="flex min-h-0 flex-1 flex-col gap-0">
            <div className="p-3 pb-0">
              <TabsList className="w-full">
                <TabsTrigger value="log">Log</TabsTrigger>
                <TabsTrigger value="traces">Traces</TabsTrigger>
                <TabsTrigger value="systems">Systems</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="log" className="mt-0 min-h-0 data-[state=active]:flex data-[state=active]:flex-col">
              <MissionLog messages={model.messages} tools={model.tools} onSuggest={suggest} />
            </TabsContent>
            <TabsContent value="traces" className="mt-0 min-h-0 overflow-y-auto overscroll-contain">
              <TracePanel traces={model.traces} />
            </TabsContent>
            <TabsContent value="systems" className="mt-0 min-h-0 overflow-y-auto overscroll-contain">
              <SystemPanels snapshot={snapshot} micActive={micActive} />
            </TabsContent>
          </Tabs>
          <Composer
            value={draft}
            onChange={setDraft}
            onSend={sendCommand}
            onMicStart={startMic}
            onMicStop={stopMic}
            micActive={micActive}
            awaitingReply={awaitingSince !== null}
            voiceEnabled={settings.voice}
            inputRef={composerInputRef}
          />
        </section>

        {/* The scroller clips at its padding edge, so -m-1.5/p-1.5 leaves room for
            the panels' outer ring and shadow; top and max-h absorb the 6px. */}
        <aside
          aria-label="Controls"
          className="flex min-h-0 flex-col gap-4 lg:sticky lg:top-[4.125rem] lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:-m-1.5 lg:max-h-[calc(100dvh-4.75rem)] lg:self-start lg:overflow-y-auto lg:overscroll-contain lg:p-1.5 xl:static xl:col-start-3 xl:row-span-1 xl:max-h-none xl:self-stretch"
        >
          {/* Safety never folds: Space/Esc are ignored while typing, so this Stop must stay one click away. */}
          <Panel title="Safety">
            <EmergencyControls onOperator={sendOperatorAction} connected={connected} />
          </Panel>
          <Panel title="Manual flight" description="Sent straight to the drone. Safety limits still apply." collapsibleId="manual-flight">
            <FlightControls onOperator={sendOperatorAction} onCommand={sendCommand} flying={isFlying} battery={battery} />
          </Panel>
          <Panel
            title={isFlying ? "Flight checks" : "Pre-flight checks"}
            aside={<VerdictBadge verdict={preflightVerdict(checks)} />}
            collapsibleId="checks"
          >
            <PreflightList checks={checks} />
          </Panel>
        </aside>
      </div>

      <QuickSafetyBar onOperator={sendOperatorAction} />
      <SafetyAlerts alerts={model.alerts} onDismiss={handleDismissAlert} />
    </div>
  );
}

/** Applies the theme to <html> and follows the system setting when asked to. */
function useTheme(theme: Theme) {
  React.useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const root = document.documentElement;

    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && media.matches);
      if (root.classList.contains("dark") === dark) {
        return;
      }
      // Swap every color at once instead of letting each transition fire.
      root.classList.add("theme-switching");
      root.classList.toggle("dark", dark);
      void window.getComputedStyle(root).opacity;
      requestAnimationFrame(() => root.classList.remove("theme-switching"));
    };

    apply();
    if (theme !== "system") {
      return;
    }
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
}

const THEMES: Array<{ value: Theme; label: string; icon: LucideIcon }> = [
  { value: "system", label: "System", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon }
];

function SettingsMenu({
  settings,
  onChange
}: {
  settings: Settings;
  onChange: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
}) {
  const toggles: Array<{ key: "camera" | "voice" | "showLatency" | "showFps"; label: string; hint: string }> = [
    { key: "camera", label: "Show camera feed", hint: "Hiding it also stops frame requests." },
    { key: "voice", label: "Voice messages", hint: "Shows the microphone button." },
    { key: "showLatency", label: "Show AI response time", hint: "Over the camera view." },
    { key: "showFps", label: "Show frame rate", hint: "Over the camera view." }
  ];

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Display settings">
          <Settings aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="flex w-80 flex-col gap-5">
        <p className="text-sm font-semibold">Display settings</p>

        <fieldset>
          <legend className="text-[13px] font-medium">Theme</legend>
          <div className="mt-2 grid grid-cols-3 gap-0.5 rounded-lg bg-muted p-0.5">
            {THEMES.map(({ value, label, icon: Icon }) => (
              <label key={value} className="relative">
                <input
                  type="radio"
                  name="theme"
                  value={value}
                  checked={settings.theme === value}
                  onChange={() => onChange("theme", value)}
                  className="peer sr-only"
                />
                <span className="flex cursor-pointer items-center justify-center gap-1.5 rounded-md py-1.5 text-[13px] text-muted-foreground transition-[background-color,color] duration-150 peer-checked:bg-card peer-checked:font-medium peer-checked:text-foreground peer-checked:shadow-border peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50 hover:text-foreground">
                  <Icon className="size-3.5" aria-hidden="true" />
                  {label}
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="flex flex-col gap-4">
          {toggles.map(({ key, label, hint }) => (
            <div key={key} className="flex items-start justify-between gap-3">
              <Label htmlFor={`set-${key}`} className="flex-col items-start gap-0.5 font-normal">
                <span className="text-sm">{label}</span>
                <span className="text-[13px] text-muted-foreground">{hint}</span>
              </Label>
              <Switch id={`set-${key}`} checked={settings[key]} onCheckedChange={(value) => onChange(key, value)} className="mt-0.5" />
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
