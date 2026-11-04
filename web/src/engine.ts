// Page-side handle to the Pyodide worker. Offers the same live-run interface as the
// server WebSocket, so Operate mode doesn't care where the simulation runs.

import type { LiveHandlers, LiveRun } from "./api";
import type { Inbound, Outbound } from "./engine.worker";
import type { DayReport, Layout, SimSettings } from "./types";

export interface EngineStatus {
  stage: "idle" | "loading" | "ready" | "error";
  message: string;
}

export interface ForecastResult {
  report: DayReport;
  seconds: number;
}

interface PendingForecast {
  resolve(result: ForecastResult): void;
  reject(error: Error): void;
  onProgress(progress: number, clock: string): void;
}

export class BrowserEngine {
  status: EngineStatus = { stage: "idle", message: "Python loads the first time you use it" };
  onStatus: (status: EngineStatus) => void = () => {};
  private worker: Worker | null = null;
  private liveHandlers: LiveHandlers | null = null;
  private pending = new Map<number, PendingForecast>();
  private nextId = 1;

  warmup() {
    this.post({ type: "warmup" });
  }

  forecast(
    layout: Layout,
    settings: SimSettings,
    onProgress: PendingForecast["onProgress"],
  ): Promise<ForecastResult> {
    this.cancelForecast();
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, onProgress });
      this.post({
        type: "forecast",
        id,
        layout: JSON.stringify(layout),
        settings: JSON.stringify(settings),
      });
    });
  }

  cancelForecast() {
    for (const p of this.pending.values()) p.reject(new Error("cancelled"));
    this.pending.clear();
    if (this.worker) this.post({ type: "cancel-forecast" });
  }

  startLive(layout: Layout, settings: SimSettings, handlers: LiveHandlers): LiveRun {
    this.liveHandlers = handlers;
    this.post({ type: "start", layout: JSON.stringify(layout), settings: JSON.stringify(settings) });
    return {
      send: (command) => this.post({ type: "command", command: JSON.stringify(command) }),
      close: () => {
        if (this.liveHandlers === handlers) this.liveHandlers = null;
        this.post({ type: "stop" });
      },
    };
  }

  private post(message: Inbound) {
    if (!this.worker) {
      this.worker = new Worker(new URL("./engine.worker.ts", import.meta.url), { type: "module" });
      this.worker.onmessage = (event: MessageEvent<Outbound>) => this.receive(event.data);
      this.worker.onerror = (event) =>
        this.setStatus({ stage: "error", message: event.message || "The engine worker crashed." });
    }
    this.worker.postMessage(message);
  }

  private setStatus(status: EngineStatus) {
    this.status = status;
    this.onStatus(status);
  }

  private receive(message: Outbound) {
    switch (message.type) {
      case "status":
        this.setStatus({ stage: message.stage, message: message.message });
        break;
      case "live": {
        const handlers = this.liveHandlers;
        if (!handlers) break;
        const data = JSON.parse(message.message);
        if (data.type === "init") handlers.onInit(data);
        else if (data.type === "frame") handlers.onFrame(data);
        else if (data.type === "report") handlers.onReport(data.report);
        else if (data.type === "error") handlers.onError(data.message);
        break;
      }
      case "progress":
        this.pending.get(message.id)?.onProgress(message.progress, message.clock);
        break;
      case "forecast": {
        const p = this.pending.get(message.id);
        this.pending.delete(message.id);
        p?.resolve({ report: JSON.parse(message.report), seconds: message.seconds });
        break;
      }
      case "forecast-error": {
        const p = this.pending.get(message.id);
        this.pending.delete(message.id);
        p?.reject(new Error(message.message));
        break;
      }
    }
  }
}
