/**
 * The transport port.
 *
 * A transport moves one signed frame from this node to another node. It knows
 * nothing about conversations, membership, ordering, or encryption: by the
 * time a frame reaches a transport it is already signed, and the records
 * inside it are already ciphertext. That is what makes P2P, SMTP and the
 * in-process bus genuinely interchangeable, and what would let a WebRTC or
 * QUIC transport drop in without touching a line of protocol code.
 *
 * Transports differ in the guarantees they can offer, and the router cares
 * about exactly two:
 *
 *   immediate  — delivery attempt completes now, or fails now (P2P, local)
 *   deferred   — accepted for later delivery; the peer may be offline (SMTP)
 */
import type { SignedFrame, TransportHint, TransportName } from "../protocol/types.ts";

export type PeerAddress = {
  peerId: string;
  hints: TransportHint[];
};

export type ConnectionInfo = {
  connectionId: string;
  transport: TransportName;
  peerId?: string;
  direction: "inbound" | "outbound";
  remote: string;
};

export type InboundFrame = {
  /** Not yet verified. The node verifies every frame at one choke point. */
  frame: SignedFrame;
  transport: TransportName;
  connectionId?: string;
  remote?: string;
  /** Milliseconds this frame spent in transit, when the transport can tell. */
  latencyMs?: number;
};

export type TransportHandlers = {
  onFrame: (inbound: InboundFrame) => void;
  onConnectionOpen?: (info: ConnectionInfo) => void;
  onConnectionClose?: (info: ConnectionInfo, reason?: string) => void;
};

export type TransportStatus = {
  name: TransportName;
  running: boolean;
  /** Human-readable: what this transport is actually doing right now. */
  detail: string;
  framesSent: number;
  framesReceived: number;
  failures: number;
  connections?: number;
};

export interface Transport {
  readonly name: TransportName;
  /** "immediate" transports fail fast; "deferred" ones accept and retry. */
  readonly delivery: "immediate" | "deferred";
  attach(handlers: TransportHandlers): void;
  start(): Promise<void>;
  stop(): Promise<void>;
  /**
   * Cheap, synchronous: is this transport worth *trying* for this peer? A
   * published address counts, because a dial is worth attempting.
   */
  canReach(peer: PeerAddress): boolean;
  /**
   * Whether a live connection to this peer exists right now. Distinct from
   * `canReach` on purpose: what the UI shows must be what is true, not what
   * might work. A transport with no connection concept omits this and the
   * router falls back to `canReach`.
   */
  connectedTo?(peer: PeerAddress): boolean;
  /** Resolves on successful hand-off; rejects if this transport cannot deliver. */
  send(peer: PeerAddress, frame: SignedFrame): Promise<void>;
  status(): TransportStatus;
  /**
   * Bind an authenticated peer id to a connection. Called by the node after
   * the handshake, because proving identity is protocol work, not transport
   * work — but the transport is what has to route the next frame.
   */
  bindPeer?(connectionId: string, peerId: string): void;
}

export class TransportUnavailableError extends Error {
  readonly transport: TransportName;

  constructor(transport: TransportName, message: string) {
    super(message);
    this.name = "TransportUnavailableError";
    this.transport = transport;
  }
}

export function hintsFor(peer: PeerAddress, kind: "p2p"): Extract<TransportHint, { kind: "p2p" }>[];
export function hintsFor(peer: PeerAddress, kind: "smtp"): Extract<TransportHint, { kind: "smtp" }>[];
export function hintsFor(peer: PeerAddress, kind: TransportHint["kind"]): TransportHint[] {
  return peer.hints.filter((hint) => hint.kind === kind);
}
