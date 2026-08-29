/**
 * Observability for an experiment that is mostly invisible.
 *
 * You cannot debug "did that message go direct or over SMTP?" from a chat
 * bubble, so every frame, rejection, transport switch and sync round is
 * counted here and shown in the diagnostics panel.
 */
export type DiagnosticEvent = {
  at: number;
  level: "info" | "warn" | "error";
  scope: string;
  message: string;
  detail?: Record<string, unknown>;
};

export type Counters = {
  framesSent: number;
  framesReceived: number;
  framesRejected: number;
  recordsSent: number;
  recordsReceived: number;
  recordsRejected: number;
  duplicatesSuppressed: number;
  messagesPosted: number;
  connectionsOpened: number;
  connectionsClosed: number;
  handshakesCompleted: number;
  handshakesRejected: number;
};

const ZERO: Counters = {
  framesSent: 0,
  framesReceived: 0,
  framesRejected: 0,
  recordsSent: 0,
  recordsReceived: 0,
  recordsRejected: 0,
  duplicatesSuppressed: 0,
  messagesPosted: 0,
  connectionsOpened: 0,
  connectionsClosed: 0,
  handshakesCompleted: 0,
  handshakesRejected: 0,
};

export class Diagnostics {
  readonly #counters: Counters = { ...ZERO };
  readonly #events: DiagnosticEvent[] = [];
  readonly #limit: number;
  readonly #listeners = new Set<(event: DiagnosticEvent) => void>();

  constructor(limit = 500) {
    this.#limit = limit;
  }

  bump<K extends keyof Counters>(counter: K, by = 1): void {
    this.#counters[counter] += by;
  }

  record(
    level: DiagnosticEvent["level"],
    scope: string,
    message: string,
    detail?: Record<string, unknown>,
  ): void {
    const event: DiagnosticEvent = {
      at: Date.now(),
      level,
      scope,
      message,
      ...(detail ? { detail } : {}),
    };
    this.#events.push(event);
    if (this.#events.length > this.#limit) this.#events.shift();
    for (const listener of this.#listeners) listener(event);
  }

  info(scope: string, message: string, detail?: Record<string, unknown>): void {
    this.record("info", scope, message, detail);
  }

  warn(scope: string, message: string, detail?: Record<string, unknown>): void {
    this.record("warn", scope, message, detail);
  }

  error(scope: string, message: string, detail?: Record<string, unknown>): void {
    this.record("error", scope, message, detail);
  }

  onEvent(listener: (event: DiagnosticEvent) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  counters(): Counters {
    return { ...this.#counters };
  }

  events(limit = 100): DiagnosticEvent[] {
    return this.#events.slice(-limit);
  }
}
