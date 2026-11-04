import type {
  DayReport,
  Frame,
  InitMessage,
  Layout,
  SimSettings,
  Template,
  Validation,
} from "./types";

async function request<T>(method: string, url: string, body?: unknown, headers?: HeadersInit) {
  const response = await fetch(url, {
    method,
    headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const getCatalog = () => request<Template[]>("GET", "/api/catalog");
export const getSample = () => request<Layout>("GET", "/api/examples/sample");
export const validate = (layout: Layout) => request<Validation>("POST", "/api/validate", layout);
export const createPark = (layout: Layout) =>
  request<{ id: string; edit_token: string }>("POST", "/api/parks", layout);
export const getPark = (id: string) =>
  request<{ id: string; layout: Layout; warnings: string[] }>(
    "GET",
    `/api/parks/${encodeURIComponent(id)}`,
  );
export const updatePark = (id: string, token: string, layout: Layout) =>
  request<{ id: string }>("PUT", `/api/parks/${encodeURIComponent(id)}`, layout, {
    "X-Edit-Token": token,
  });

export interface LiveHandlers {
  onInit(message: InitMessage): void;
  onFrame(frame: Frame): void;
  onReport(report: DayReport): void;
  onError(message: string): void;
  onClose(): void;
}

/** A running simulated day, wherever it runs (server WebSocket or in-browser Python). */
export interface LiveRun {
  send(command: Record<string, unknown>): void;
  close(): void;
}

/** One simulated day run on the server and streamed over a WebSocket. */
export class LiveConnection implements LiveRun {
  private ws: WebSocket;
  private closedByUs = false;

  constructor(layout: Layout, settings: SimSettings, handlers: LiveHandlers) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${scheme}://${location.host}/api/live`);
    this.ws.onopen = () => this.send({ type: "start", layout, settings });
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "init") handlers.onInit(message);
      else if (message.type === "frame") handlers.onFrame(message);
      else if (message.type === "report") handlers.onReport(message.report);
      else if (message.type === "error") handlers.onError(message.message);
    };
    this.ws.onerror = () => handlers.onError("Lost connection to the simulation server.");
    this.ws.onclose = () => {
      if (!this.closedByUs) handlers.onClose();
    };
  }

  send(command: Record<string, unknown>) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(command));
  }

  close() {
    this.closedByUs = true;
    this.ws.close();
  }
}

// Edit tokens live only in this browser: whoever created a park can keep editing it.
const TOKENS_KEY = "parksim.tokens";

export function loadToken(id: string): string | null {
  try {
    return JSON.parse(localStorage.getItem(TOKENS_KEY) ?? "{}")[id] ?? null;
  } catch {
    return null;
  }
}

export function saveToken(id: string, token: string) {
  try {
    const tokens = JSON.parse(localStorage.getItem(TOKENS_KEY) ?? "{}");
    tokens[id] = token;
    localStorage.setItem(TOKENS_KEY, JSON.stringify(tokens));
  } catch {
    /* private mode: editing still works this session */
  }
}
