import { Activity, Camera, ChevronDown, Gauge, Mic, Plane, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConversationPanel } from "@/features/chat/ConversationPanel";
import { FlightControls } from "@/features/controls/FlightControls";
import { SystemPanels } from "@/features/telemetry/SystemPanels";
import { TelemetryStrip } from "@/features/telemetry/TelemetryStrip";
import { TracePanel } from "@/features/traces/TracePanel";
import { VideoStage } from "@/features/video/VideoStage";
import { fetchTraces, fetchVideoStatus, sendJson } from "@/lib/api";
import { PushToTalkAudio } from "@/lib/audio";
import {
  createDashboardModel,
  mergeFetchedTraces,
  reduceDashboardMessage,
  type DashboardMessage
} from "@/lib/dashboard-state";
import { buildWebSocketUrl, getStateMetric } from "@/lib/format";
import { cn } from "@/lib/utils";
import { createFrameRequestLoop, type FrameRequestLoop } from "@/lib/video-stream";
import type { ConnectionState, DashboardSnapshot, VideoStatus } from "@/types";

type Settings = {
  camera: boolean;
  voice: boolean;
  showLatency: boolean;
  showFps: boolean;
};

const DEFAULT_SETTINGS: Settings = { camera: true, voice: true, showLatency: true, showFps: true };
const SETTINGS_KEY = "telloai.settings";

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
  const [missionClock, setMissionClock] = React.useState("00:00:00");
  const [settings, setSettings] = React.useState<Settings>(loadSettings);
  const [fps, setFps] = React.useState(0);
  const [latencyMs, setLatencyMs] = React.useState<number | null>(null);
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
      const socket = new WebSocket(buildWebSocketUrl(window.location.protocol, window.location.host));
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
    const start = Date.now();
    const tick = () => {
      const elapsed = Math.floor((Date.now() - start) / 1000);
      const hh = String(Math.floor(elapsed / 3600)).padStart(2, "0");
      const mm = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
      const ss = String(elapsed % 60).padStart(2, "0");
      setMissionClock(`${hh}:${mm}:${ss}`);
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);

  const sendCommand = React.useCallback(
    (text: string) => {
      setLatencyMs(null);
      const sent = sendJson(socketRef.current, { type: "command", text });
      if (!sent) {
        appendSystemMessage("Command not sent — the dashboard socket is offline.");
      }
    },
    [appendSystemMessage]
  );

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
    sendJson(socketRef.current, { type: "audio_end" });
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
  const lowBattery = battery !== undefined && battery <= 20;
  const connected = connection === "connected";

  const connDot =
    connection === "connected" ? "bg-success" : connection === "connecting" ? "bg-warning" : "bg-destructive";

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-screen flex-col bg-background">
        {/* Header */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Plane className="size-4 -rotate-45" />
            </span>
            <span className="text-sm font-semibold tracking-tight">TELLOAI</span>
          </div>

          <div className="ml-2 flex items-center gap-1.5">
            <Badge variant="outline" className="gap-1.5 font-normal text-muted-foreground">
              <span className={cn("size-1.5 rounded-full", connDot)} />
              {connection}
            </Badge>
            <Badge variant="outline" className="gap-1.5 font-normal text-muted-foreground">
              <span className={cn("size-1.5 rounded-full", model.mode === "live" ? "bg-primary" : "bg-muted-foreground/60")} />
              {model.mode === "unknown" ? "agent pending" : model.mode}
            </Badge>
            {lowBattery ? (
              <Badge variant="destructive" className="font-normal">
                low battery
              </Badge>
            ) : null}
          </div>

          <div className="ml-auto flex items-center gap-3">
            <div className="flex items-center gap-1.5 font-mono text-xs tabular-nums text-muted-foreground">
              <span className="text-muted-foreground/60">T+</span>
              {missionClock}
            </div>
            <SettingsMenu settings={settings} onChange={setSetting} />
          </div>
        </header>

        {/* Body */}
        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_400px] lg:overflow-hidden">
          <main className="flex min-h-0 flex-col gap-4 p-4">
            <VideoStage
              frameSrc={model.frameSrc}
              videoStatus={videoStatus}
              cameraOn={settings.camera}
              fps={settings.showFps ? fps : null}
              latencyMs={settings.showLatency ? latencyMs : null}
            />
            <TelemetryStrip state={model.state} />
          </main>

          <aside className="flex min-h-0 flex-col gap-4 border-t p-4 lg:border-t-0 lg:border-l">
            <Collapsible defaultOpen className="shrink-0 overflow-hidden rounded-xl border bg-card">
              <CollapsibleTrigger className="group flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-accent/40">
                <span className="text-sm font-semibold">Flight Controls</span>
                <span className="ml-auto font-mono text-xs text-muted-foreground">{connected ? "ready" : "offline"}</span>
                <ChevronDown className="size-4 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180" />
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="px-4 pb-4">
                  <FlightControls onCommand={sendCommand} connected={connected} />
                </div>
              </CollapsibleContent>
            </Collapsible>

            <Card className="flex min-h-0 flex-1 flex-col gap-0 overflow-hidden py-0 max-lg:min-h-[480px]">
              <Tabs defaultValue="chat" className="flex min-h-0 flex-1 flex-col gap-0">
                <div className="p-3 pb-0">
                  <TabsList className="grid w-full grid-cols-3">
                    <TabsTrigger value="chat">Chat</TabsTrigger>
                    <TabsTrigger value="systems">Systems</TabsTrigger>
                    <TabsTrigger value="traces">Traces</TabsTrigger>
                  </TabsList>
                </div>

                <TabsContent value="chat" className="mt-0 min-h-0 data-[state=active]:flex data-[state=active]:flex-col">
                  <ConversationPanel
                    messages={model.messages}
                    onSend={sendCommand}
                    onMicStart={startMic}
                    onMicStop={stopMic}
                    micActive={micActive}
                    connected={connected}
                    voiceEnabled={settings.voice}
                  />
                </TabsContent>

                <TabsContent value="systems" className="mt-0 min-h-0 overflow-hidden">
                  <ScrollArea className="h-full">
                    <SystemPanels snapshot={snapshot} micActive={micActive} />
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="traces" className="mt-0 min-h-0 overflow-hidden">
                  <ScrollArea className="h-full">
                    <TracePanel traces={model.traces} tools={model.tools} />
                  </ScrollArea>
                </TabsContent>
              </Tabs>
            </Card>
          </aside>
        </div>
      </div>
    </TooltipProvider>
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
          <p className="text-xs text-muted-foreground">Toggle inputs and on-screen telemetry.</p>
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
              <Switch
                id={`set-${key}`}
                checked={settings[key]}
                onCheckedChange={(value) => onChange(key, value)}
              />
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
