import { Activity, Camera, ClipboardCheck, Compass, Crosshair, Gamepad2, Gauge, Mic, OctagonX, ScanLine, Settings, ShieldAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CommandDeck } from "@/features/chat/CommandDeck";
import { EmergencyControls, FlightControls, QuickSafetyBar } from "@/features/controls/FlightControls";
import { MissionLog } from "@/features/log/MissionLog";
import { NavigationPanel } from "@/features/map/TacticalMap";
import { SafetyAlerts } from "@/features/safety/SafetyAlerts";
import { SafetyBanners } from "@/features/safety/SafetyBanners";
import { PreflightList, VerdictBadge, preflightChecks, preflightVerdict } from "@/features/safety/PreflightPanel";
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

type Settings = {
  camera: boolean;
  voice: boolean;
  hud: boolean;
  showLatency: boolean;
  showFps: boolean;
};

const DEFAULT_SETTINGS: Settings = { camera: true, voice: true, hud: true, showLatency: true, showFps: true };
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
      connection,
      state: model.state,
      videoStatus,
      tools: model.tools,
      traces: model.traces
    }),
    [connection, model.mode, model.state, model.tools, model.traces, videoStatus]
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
    linkLost,
    battery,
    video: videoStatus.active && settings.camera,
    mode: model.mode,
    operatorHold
  });

  return (
    <TooltipProvider delayDuration={200}>
      <div className="deck-backdrop flex min-h-screen flex-col lg:h-screen lg:overflow-hidden">
        <TopBar
          connection={connection}
          mode={model.mode}
          flying={isFlying}
          linkLost={linkLost}
          operatorHold={operatorHold}
          battery={battery}
          flightSeconds={flightSeconds}
          settings={<SettingsMenu settings={settings} onChange={setSetting} />}
        />

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 pb-24 lg:grid-cols-[minmax(0,1fr)_372px] lg:grid-rows-[auto_minmax(0,1fr)_minmax(0,220px)] lg:pb-3 xl:grid-cols-[minmax(300px,340px)_minmax(0,1fr)_372px] xl:grid-rows-[auto_minmax(0,1fr)]">
          {/* Centre: what the drone sees, and what you tell it. */}
          <main className="flex min-h-0 min-w-0 flex-col gap-3 lg:col-start-1 lg:row-span-2 lg:row-start-1 xl:col-start-2">
            <SafetyBanners operatorHold={operatorHold} onResume={handleResume} />
            <VideoStage
              frameSrc={model.frameSrc}
              videoStatus={videoStatus}
              cameraOn={settings.camera}
              fps={settings.showFps ? fps : null}
              latencyMs={settings.showLatency ? latencyMs : null}
              linkLost={linkLost}
              showHud={settings.hud}
              telemetry={{ heading, height, speed, x: position.x, y: position.y, z: position.z, flying: isFlying }}
            />
            <CommandDeck
              onSend={sendCommand}
              onMicStart={startMic}
              onMicStop={stopMic}
              micActive={micActive}
              connected={connected}
              awaitingReply={awaitingSince !== null}
              voiceEnabled={settings.voice}
              mode={model.mode}
            />
          </main>

          {/* Right rail: safety first, then hands-on flying. Never scrolls on desktop. */}
          <aside
            aria-label="Flight systems"
            className="flex min-h-0 flex-col gap-3 lg:col-start-2 lg:row-start-1 lg:overflow-y-auto xl:col-start-3 xl:row-span-2 xl:overflow-visible [scrollbar-width:thin]"
          >
            <Panel title="Safety" icon={ShieldAlert} aside={<ConsoleState connected={connected} />}>
              <EmergencyControls onOperator={sendOperatorAction} connected={connected} />
            </Panel>
            <Panel title="Flight Controls" icon={Gamepad2} aside={<span className="font-mono text-[10px] text-muted-foreground">direct · no AI</span>}>
              <FlightControls onOperator={sendOperatorAction} onCommand={sendCommand} flying={isFlying} battery={battery} />
            </Panel>
            <Panel
              title={isFlying ? "Flight checks" : "Pre-flight"}
              icon={ClipboardCheck}
              aside={<VerdictBadge verdict={preflightVerdict(checks)} />}
              className="min-h-0 shrink"
              bodyClassName="min-h-0 overflow-y-auto py-1"
            >
              <PreflightList checks={checks} />
            </Panel>
          </aside>

          {/* Where it is: above the log on wide screens, under the rail otherwise. */}
          <Panel
            title="Navigation"
            icon={Compass}
            aside={<span className="font-mono text-[10px] text-muted-foreground">↑ = takeoff hdg</span>}
            className="min-h-0 lg:col-start-2 lg:row-span-2 lg:row-start-2 lg:overflow-y-auto xl:col-start-1 xl:row-span-1 xl:row-start-1 xl:overflow-visible"
          >
            <NavigationPanel x={position.x} y={position.y} heading={heading} height={height} trail={trail} flying={isFlying} />
          </Panel>

          {/* Mission log: left column on wide screens, under the camera otherwise. */}
          <section
            aria-label="Mission record"
            className="panel flex min-h-0 flex-col overflow-hidden max-lg:h-[560px] lg:col-start-1 lg:row-start-3 xl:col-start-1 xl:row-start-2"
          >
            <Tabs defaultValue="log" className="flex min-h-0 flex-1 flex-col gap-0">
              <div className="flex h-11 shrink-0 items-center border-b px-2">
                <TabsList className="h-8 w-full bg-sunken">
                  <TabsTrigger value="log" className="text-xs">
                    <ScanLine />
                    Log
                  </TabsTrigger>
                  <TabsTrigger value="traces" className="text-xs">
                    <Activity />
                    Traces
                  </TabsTrigger>
                  <TabsTrigger value="systems" className="text-xs">
                    <Crosshair />
                    Systems
                  </TabsTrigger>
                </TabsList>
              </div>

              <TabsContent value="log" className="mt-0 min-h-0 data-[state=active]:flex data-[state=active]:flex-1 data-[state=active]:flex-col">
                <MissionLog messages={model.messages} tools={model.tools} />
              </TabsContent>
              <TabsContent value="traces" className="mt-0 min-h-0 flex-1 overflow-y-auto">
                <TracePanel traces={model.traces} />
              </TabsContent>
              <TabsContent value="systems" className="mt-0 min-h-0 flex-1 overflow-y-auto">
                <SystemPanels snapshot={snapshot} micActive={micActive} />
              </TabsContent>
            </Tabs>
          </section>
        </div>

        <QuickSafetyBar onOperator={sendOperatorAction} />
        <SafetyAlerts alerts={model.alerts} onDismiss={handleDismissAlert} />
      </div>
    </TooltipProvider>
  );
}

function ConsoleState({ connected }: { connected: boolean }) {
  return (
    <span className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
      <OctagonX className={connected ? "size-3 text-success" : "size-3 text-destructive"} />
      {connected ? "armed · reaches drone" : "offline"}
    </span>
  );
}

function SettingsMenu({
  settings,
  onChange
}: {
  settings: Settings;
  onChange: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
}) {
  const rows: Array<{ key: keyof Settings; label: string; hint: string; icon: LucideIcon }> = [
    { key: "camera", label: "Camera feed", hint: "Live video stream", icon: Camera },
    { key: "hud", label: "Flight HUD", hint: "Heading, altitude, reticle", icon: Crosshair },
    { key: "voice", label: "Voice input", hint: "Push-to-talk mic", icon: Mic },
    { key: "showLatency", label: "Latency", hint: "Model response time", icon: Gauge },
    { key: "showFps", label: "FPS", hint: "Feed frame rate", icon: Activity }
  ];

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Settings">
          <Settings className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">Console settings</p>
          <p className="text-xs text-muted-foreground">Toggle inputs and on-screen instruments.</p>
        </div>
        <Separator className="my-3" />
        <div className="space-y-3">
          {rows.map(({ key, label, hint, icon: Icon }) => (
            <div key={key} className="flex items-center justify-between gap-3">
              <Label htmlFor={`set-${key}`} className="cursor-pointer items-start gap-2.5 font-normal">
                <Icon className="mt-0.5 size-4 text-muted-foreground" />
                <span className="flex flex-col">
                  <span>{label}</span>
                  <span className="text-xs font-normal text-muted-foreground">{hint}</span>
                </span>
              </Label>
              <Switch id={`set-${key}`} checked={settings[key]} onCheckedChange={(value) => onChange(key, value)} />
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
