/**
 * The LinkChat node.
 *
 * One node per participant. It owns the identity, the conversation logs, and
 * the transports, and it is the single place where a frame arriving from the
 * network is verified before anything else in the system sees it.
 *
 * Why a node process rather than a browser tab talking WebRTC directly:
 * a browser cannot listen for an inbound connection, cannot receive SMTP, and
 * cannot keep a private key out of the reach of the page. The node holds the
 * key and the transports; the browser UI is a local client of it. A WebRTC
 * transport can be added later behind the same Transport interface, and the
 * signalling it needs is exactly the rendezvous problem documented in
 * docs/LINKCHAT_PROTOCOL.md.
 */
import { EventEmitter } from "node:events";
import { mkdirSync } from "node:fs";
import { toBase64Url } from "../crypto/encoding.ts";
import { randomBytes } from "../crypto/primitives.ts";
import { Conversation } from "../conversation/conversation.ts";
import type { ConversationState } from "../conversation/state.ts";
import { Diagnostics } from "../diagnostics/metrics.ts";
import { Identity } from "../identity/identity.ts";
import { Keystore } from "../identity/keystore.ts";
import { ProtocolError } from "../protocol/errors.ts";
import { ReplayGuard, signFrame, verifyFrame } from "../protocol/frames.ts";
import { DEFAULT_LINK_ORIGIN, parseInviteLink } from "../protocol/invite.ts";
import { verifyWithPublicKey } from "../identity/identity.ts";
import { fromBase64Url } from "../crypto/encoding.ts";
import { canonicalBytes } from "../crypto/encoding.ts";
import type {
  AuthFrame,
  HelloFrame,
  SignedFrame,
  SignedRecord,
  TransportHint,
  WireFrame,
} from "../protocol/types.ts";
import { deriveStorageKey, FileStorage } from "../storage/file-storage.ts";
import { MemoryStorage } from "../storage/memory-storage.ts";
import type { ConversationStorage } from "../storage/types.ts";
import {
  SyncTracker,
  buildSyncRequest,
  buildSyncResponse,
  ingestBatch,
} from "../sync/sync-engine.ts";
import { LocalTransport, type LocalBus } from "../transports/local/local-transport.ts";
import { P2PTransport, type P2POptions } from "../transports/p2p/p2p-transport.ts";
import { TransportRouter, type PeerRoute } from "../transports/router.ts";
import { SmtpTransport, type SmtpTransportOptions } from "../transports/smtp/smtp-transport.ts";
import type { InboundFrame, PeerAddress, Transport } from "../transports/transport.ts";

export type NodeConfig = {
  displayName: string;
  dataDir?: string;
  /** Keep everything in memory: nothing is written, nothing survives exit. */
  ephemeral?: boolean;
  keyPassphrase?: string;
  linkOrigin?: string;
  p2p?: P2POptions | false;
  smtp?: SmtpTransportOptions | false;
  localBus?: LocalBus;
  intervals?: {
    syncMs?: number;
    reconnectMs?: number;
    outboxMs?: number;
    pingMs?: number;
    /**
     * Minimum gap between anti-entropy requests sent to a peer over a
     * deferred transport. Every one of those is a real email: polling a peer
     * over SMTP every few seconds would burn provider rate limits and look
     * exactly like abuse. Direct peers are polled at `syncMs`.
     */
    deferredSyncMs?: number;
  };
};

export type ConversationView = {
  conversationId: string;
  title: string;
  createdAt: string;
  state: ConversationState;
  routes: PeerRoute[];
  /** Aggregate badge for the UI: how this conversation is currently carried. */
  connection: "direct" | "smtp" | "local" | "offline";
  /** Shown alongside the badge; a sync in flight does not change the route. */
  syncing: boolean;
  inviteLink: string | null;
  inviteUri: string | null;
};

type PendingHandshake = {
  challenge: string;
  conversationId: string;
  connectionId?: string;
};

export class LinkChatNode extends EventEmitter {
  readonly identity: Identity;
  readonly diagnostics = new Diagnostics();
  readonly #storage: ConversationStorage;
  readonly #router: TransportRouter;
  readonly #conversations = new Map<string, Conversation>();
  readonly #sync = new SyncTracker();
  readonly #replay = new ReplayGuard();
  readonly #pending = new Map<string, PendingHandshake>();
  readonly #authenticated = new Map<string, string>();
  readonly #config: NodeConfig;
  readonly #linkOrigin: string;
  readonly #p2p: P2PTransport | null;
  readonly #smtp: SmtpTransport | null;
  readonly #timers: NodeJS.Timeout[] = [];
  readonly #lastDeferredSync = new Map<string, number>();
  #started = false;

  constructor(config: NodeConfig) {
    super();
    this.#config = config;
    this.#linkOrigin = config.linkOrigin ?? DEFAULT_LINK_ORIGIN;

    if (config.ephemeral || !config.dataDir) {
      this.identity = Identity.generate(config.displayName);
      this.#storage = new MemoryStorage();
    } else {
      mkdirSync(config.dataDir, { recursive: true });
      const keystore = new Keystore(config.dataDir, config.keyPassphrase);
      this.identity = keystore.loadOrCreate(config.displayName);
      this.#storage = new FileStorage(
        config.dataDir,
        deriveStorageKey(this.identity.exportPrivateKey()),
      );
    }

    const transports: Transport[] = [];
    this.#p2p = config.p2p === false ? null : new P2PTransport(config.p2p ?? {});
    if (this.#p2p) transports.push(this.#p2p);
    this.#smtp = config.smtp ? new SmtpTransport(config.smtp) : null;
    if (this.#smtp) transports.push(this.#smtp);
    if (config.localBus) transports.push(new LocalTransport(config.localBus, this.identity.peerId));

    this.#router = new TransportRouter({ transports });
    this.#router.attach({
      onFrame: (inbound) => void this.#onFrame(inbound),
      onConnectionOpen: (info) => {
        this.diagnostics.bump("connectionsOpened");
        this.diagnostics.info("p2p", `connection open (${info.direction}) ${info.remote}`);
      },
      onConnectionClose: (info) => {
        this.diagnostics.bump("connectionsClosed");
        if (info.peerId) this.#authenticated.delete(info.connectionId);
        this.diagnostics.info("p2p", `connection closed ${info.peerId ?? info.remote}`);
        this.emit("update");
      },
    });
  }

  // --- lifecycle ---------------------------------------------------------

  async start(): Promise<void> {
    if (this.#started) return;
    await this.#router.start();
    this.#started = true;

    // Re-advertise on every start: ports move, a transport may have been
    // turned off since last time, and peers need to know before they can
    // route to us.
    const readvertised: [Conversation, SignedRecord][] = [];
    for (const conversation of Conversation.openAll(this.#deps())) {
      this.#conversations.set(conversation.id, conversation);
      const profile = conversation.updateHints(this.selfHints());
      if (profile) readvertised.push([conversation, profile]);
    }

    const intervals = this.#config.intervals ?? {};
    this.#every(intervals.syncMs ?? 5000, () => void this.syncAll());
    this.#every(intervals.reconnectMs ?? 7000, () => void this.reconnectAll());
    this.#every(intervals.outboxMs ?? 3000, () => void this.#router.flushOutbox());
    this.#every(intervals.pingMs ?? 10000, () => this.#p2p?.ping());

    for (const [conversation, record] of readvertised) {
      void this.#fanOut(conversation, [record], null);
    }

    this.diagnostics.info("node", `started as ${this.identity.displayName} (${this.identity.peerId})`);
    if (this.#smtp) {
      const check = await this.#smtp.verifyRelay();
      this.diagnostics.record(check.ok ? "info" : "warn", "smtp", check.detail);
    }
    this.emit("update");
  }

  async stop(): Promise<void> {
    for (const timer of this.#timers) clearInterval(timer);
    this.#timers.length = 0;
    await this.#router.stop();
    this.#started = false;
    this.diagnostics.info("node", "stopped");
  }

  #every(ms: number, task: () => void): void {
    const timer = setInterval(task, ms);
    timer.unref();
    this.#timers.push(timer);
  }

  #deps(): { identity: Identity; storage: ConversationStorage; linkOrigin: string } {
    return { identity: this.identity, storage: this.#storage, linkOrigin: this.#linkOrigin };
  }

  /** How this node advertises itself. Only transports that are running. */
  selfHints(): TransportHint[] {
    const hints: TransportHint[] = [];
    if (this.#p2p && this.#p2p.port > 0) hints.push({ kind: "p2p", url: this.#p2p.advertisedUrl });
    if (this.#smtp) hints.push({ kind: "smtp", address: this.#smtp.address });
    return hints;
  }

  // --- conversations -----------------------------------------------------

  createConversation(title = "Conversation"): { conversation: Conversation; link: string } {
    const conversation = Conversation.create(this.#deps(), {
      title,
      hints: this.selfHints(),
    });
    this.#conversations.set(conversation.id, conversation);
    const { link } = conversation.createInvite({});
    this.diagnostics.info("conversation", `created ${conversation.id}`);
    this.emit("update");
    return { conversation, link };
  }

  /**
   * Join by link. The local records are written immediately; membership is
   * only real once a participant has validated the invite, which happens as
   * soon as one of the addresses in the link answers.
   */
  async joinByLink(link: string): Promise<Conversation> {
    const parsed = parseInviteLink(link);
    const { conversation, hints } = Conversation.joinFromLink(this.#deps(), {
      link,
      hints: this.selfHints(),
    });
    this.#conversations.set(conversation.id, conversation);
    this.diagnostics.info("conversation", `joining ${conversation.id} via link`);

    const bootstrap: PeerAddress = {
      peerId: parsed.invitedBy ?? "p_unknown",
      hints,
    };
    await this.#bootstrap(conversation, bootstrap);
    this.emit("update");
    return conversation;
  }

  /** Reach out to whoever the link pointed at, over whatever they published. */
  async #bootstrap(conversation: Conversation, peer: PeerAddress): Promise<void> {
    const joinRecords = conversation
      .records()
      .filter((record) => record.header.sender_id === this.identity.peerId);

    let reachedDirectly = false;
    if (this.#p2p) {
      for (const hint of peer.hints) {
        if (hint.kind !== "p2p") continue;
        try {
          const info = await this.#p2p.connectToUrl(hint.url);
          if (peer.peerId.startsWith("p_") && peer.peerId !== "p_unknown") {
            this.#p2p.bindPeer(info.connectionId, peer.peerId);
          }
          this.#sendHello(conversation.id, info.connectionId);
          this.#sendOnConnection(info.connectionId, {
            kind: "records",
            conversation_id: conversation.id,
            records: joinRecords,
          });
          this.#sendOnConnection(info.connectionId, buildSyncRequest(conversation));
          reachedDirectly = true;
          this.diagnostics.info("p2p", `dialled ${hint.url} for ${conversation.id}`);
        } catch (error) {
          this.diagnostics.warn("p2p", `dial ${hint.url} failed: ${(error as Error).message}`);
        }
      }
    }

    // SMTP is not an "if all else fails" afterthought here: sending the join
    // over both paths is what lets someone join a conversation whose members
    // are all offline. The duplicate is suppressed by message id.
    if (!reachedDirectly || peer.hints.some((hint) => hint.kind === "smtp")) {
      await this.#send(peer, {
        kind: "records",
        conversation_id: conversation.id,
        records: joinRecords,
      });
      await this.#send(peer, buildSyncRequest(conversation));
    }
  }

  conversations(): Conversation[] {
    return [...this.#conversations.values()];
  }

  conversation(conversationId: string): Conversation | undefined {
    return this.#conversations.get(conversationId);
  }

  async post(conversationId: string, text: string): Promise<SignedRecord> {
    const conversation = this.#require(conversationId);
    const record = conversation.post(text);
    this.diagnostics.bump("messagesPosted");
    await this.#fanOut(conversation, [record], null);
    this.emit("update");
    return record;
  }

  createInvite(conversationId: string, ttlMs?: number): { link: string; uri: string } {
    const conversation = this.#require(conversationId);
    const { link, uri } = conversation.createInvite(ttlMs === undefined ? {} : { ttlMs });
    return { link, uri };
  }

  async revokeInvite(conversationId: string, nonce: string): Promise<void> {
    const conversation = this.#require(conversationId);
    const record = conversation.revokeInvite(nonce);
    await this.#fanOut(conversation, [record], null);
    this.emit("update");
  }

  #require(conversationId: string): Conversation {
    const conversation = this.#conversations.get(conversationId);
    if (!conversation) throw new Error(`no conversation ${conversationId} on this node`);
    return conversation;
  }

  // --- peers and fan-out -------------------------------------------------

  /**
   * Everyone we should push to. Group delivery is a flood over whatever
   * transport each peer is reachable by, with duplicate suppression at the
   * log. No full mesh is assumed: a peer we cannot reach still gets the
   * message from someone who can, or asks for it on their next sync.
   */
  peersOf(conversation: Conversation, exclude: string[] = []): PeerAddress[] {
    const excluded = new Set([this.identity.peerId, ...exclude]);
    return conversation
      .state()
      .participants.filter((participant) => !excluded.has(participant.peerId) && !participant.hasLeft)
      .map((participant) => ({ peerId: participant.peerId, hints: participant.transports }));
  }

  async #fanOut(
    conversation: Conversation,
    records: SignedRecord[],
    origin: string | null,
  ): Promise<void> {
    if (records.length === 0) return;
    const peers = this.peersOf(conversation, origin ? [origin] : []);
    if (peers.length === 0) return;
    const frame = this.#sign({
      kind: "records",
      conversation_id: conversation.id,
      records,
    });
    const results = await this.#router.broadcast(peers, frame);
    for (const [peerId, result] of results) {
      this.diagnostics.bump(result.ok ? "framesSent" : "framesRejected", 0);
      if (result.ok) {
        this.diagnostics.bump("framesSent");
        this.diagnostics.bump("recordsSent", records.length);
        this.diagnostics.info("route", `${records.length} record(s) to ${peerId} via ${result.transport}`);
      } else {
        this.diagnostics.warn("route", `queued for ${peerId}: ${result.error ?? "unreachable"}`);
      }
    }
  }

  #sign(frame: WireFrame): SignedFrame {
    return signFrame(this.identity, frame);
  }

  async #send(peer: PeerAddress, frame: WireFrame): Promise<void> {
    const result = await this.#router.send(peer, this.#sign(frame));
    if (result.ok) this.diagnostics.bump("framesSent");
  }

  #sendOnConnection(connectionId: string, frame: WireFrame): void {
    if (!this.#p2p) return;
    try {
      this.#p2p.sendOnConnection(connectionId, this.#sign(frame));
      this.diagnostics.bump("framesSent");
    } catch (error) {
      this.diagnostics.warn("p2p", `send on ${connectionId} failed: ${(error as Error).message}`);
    }
  }

  #sendHello(conversationId: string, connectionId: string): void {
    const challenge = toBase64Url(randomBytes(16));
    this.#pending.set(challenge, { challenge, conversationId, connectionId });
    const hello: HelloFrame = {
      kind: "hello",
      peer_id: this.identity.peerId,
      public_key: toBase64Url(this.identity.publicKey),
      display_name: this.identity.displayName,
      conversation_id: conversationId,
      challenge,
      transports: this.selfHints(),
    };
    this.#sendOnConnection(connectionId, hello);
  }

  // --- inbound -----------------------------------------------------------

  async #onFrame(inbound: InboundFrame): Promise<void> {
    this.diagnostics.bump("framesReceived");
    let frame: WireFrame;
    try {
      frame = verifyFrame(inbound.frame, { replayGuard: this.#replay });
    } catch (error) {
      this.diagnostics.bump("framesRejected");
      const code = error instanceof ProtocolError ? error.code : "unknown";
      this.diagnostics.warn("frame", `rejected ${code}: ${(error as Error).message}`);
      return;
    }
    const senderId = inbound.frame.sender_id;

    try {
      switch (frame.kind) {
        case "hello":
          this.#onHello(frame, senderId, inbound);
          break;
        case "auth":
          await this.#onAuth(frame, senderId, inbound);
          break;
        case "records":
          await this.#onRecords(frame.conversation_id, frame.records, senderId);
          break;
        case "sync_request":
          await this.#onSyncRequest(frame, senderId, inbound);
          break;
        case "sync_response":
          this.#sync.responseReceived(senderId, { added: [], duplicates: 0, conflicts: 0, rejected: [] });
          await this.#onRecords(frame.conversation_id, frame.records, senderId);
          if (!frame.complete) {
            const conversation = this.#conversations.get(frame.conversation_id);
            if (conversation) await this.#askPeerToSync(conversation, senderId, inbound);
          }
          break;
        case "ack":
          this.diagnostics.info("ack", `${senderId} acknowledged ${frame.message_ids.length}`);
          break;
        case "presence":
          this.diagnostics.info("presence", `${senderId} is ${frame.online ? "online" : "offline"}`);
          this.emit("update");
          break;
      }
    } catch (error) {
      this.diagnostics.error("frame", `handling ${frame.kind} failed: ${(error as Error).message}`);
    }
  }

  #onHello(frame: HelloFrame, senderId: string, inbound: InboundFrame): void {
    if (frame.peer_id !== senderId) {
      this.diagnostics.bump("handshakesRejected");
      this.diagnostics.warn("handshake", "hello peer_id does not match the frame signer");
      return;
    }
    if (inbound.connectionId && this.#p2p) {
      this.#p2p.bindPeer(inbound.connectionId, senderId);
    }
    // Answer the challenge. The signature is over the challenge bound to our
    // own peer id, so it cannot be replayed as somebody else's answer.
    const accepted = this.#conversations.has(frame.conversation_id);
    const auth: AuthFrame = {
      kind: "auth",
      peer_id: this.identity.peerId,
      public_key: toBase64Url(this.identity.publicKey),
      challenge: frame.challenge,
      signature: toBase64Url(this.identity.sign(challengeBytes(frame.challenge, this.identity.peerId))),
      accepted,
      ...(accepted ? {} : { reason: "unknown conversation" }),
    };
    this.#replyTo(inbound, senderId, auth);

    // Offer our own challenge so the handshake is mutual, then let the
    // exchange settle before pushing records.
    if (accepted && inbound.connectionId && !this.#authenticated.has(inbound.connectionId)) {
      this.#sendHello(frame.conversation_id, inbound.connectionId);
    }
  }

  async #onAuth(frame: AuthFrame, senderId: string, inbound: InboundFrame): Promise<void> {
    const pending = this.#pending.get(frame.challenge);
    if (!pending) {
      this.diagnostics.bump("handshakesRejected");
      this.diagnostics.warn("handshake", "auth answered a challenge we did not issue");
      return;
    }
    this.#pending.delete(frame.challenge);
    if (frame.peer_id !== senderId) {
      this.diagnostics.bump("handshakesRejected");
      return;
    }
    const ok = verifyWithPublicKey(
      challengeBytes(frame.challenge, senderId),
      fromBase64Url(frame.signature),
      fromBase64Url(frame.public_key),
    );
    if (!ok) {
      this.diagnostics.bump("handshakesRejected");
      this.diagnostics.warn("handshake", `${senderId} failed the challenge`);
      return;
    }
    this.diagnostics.bump("handshakesCompleted");
    if (inbound.connectionId) {
      this.#authenticated.set(inbound.connectionId, senderId);
      if (this.#p2p) this.#p2p.bindPeer(inbound.connectionId, senderId);
    }
    if (!frame.accepted) {
      this.diagnostics.warn("handshake", `${senderId} declined: ${frame.reason ?? "no reason"}`);
      return;
    }

    const conversation = this.#conversations.get(pending.conversationId);
    if (!conversation) return;
    // Push our own membership records, then ask for whatever we are missing.
    const mine = conversation
      .records()
      .filter((record) => record.header.sender_id === this.identity.peerId && record.header.type !== "text");
    this.#replyTo(inbound, senderId, {
      kind: "records",
      conversation_id: conversation.id,
      records: mine,
    });
    await this.#askPeerToSync(conversation, senderId, inbound);
    this.emit("update");
  }

  async #askPeerToSync(
    conversation: Conversation,
    peerId: string,
    inbound?: InboundFrame,
  ): Promise<void> {
    this.#sync.requestSent(peerId);
    const request = buildSyncRequest(conversation);
    if (inbound) this.#replyTo(inbound, peerId, request);
    else await this.#send(this.#addressOf(conversation, peerId), request);
  }

  async #onSyncRequest(
    frame: { kind: "sync_request"; conversation_id: string; watermarks: Record<string, number> },
    senderId: string,
    inbound: InboundFrame,
  ): Promise<void> {
    const conversation = this.#conversations.get(frame.conversation_id);
    if (!conversation) return;

    // Serve history to admitted participants. If we do not yet hold the
    // genesis record we cannot evaluate admission at all, and refusing would
    // deadlock two peers who both just joined - so we serve. The cost is
    // bounded: what we serve is ciphertext, useless without the key that only
    // the link carries.
    const state = conversation.state();
    const participant = state.participants.find((entry) => entry.peerId === senderId);
    const canEvaluate = state.inviteSecret !== null;
    if (canEvaluate && participant && !participant.admitted) {
      this.diagnostics.warn("sync", `refused history to unadmitted ${senderId}`);
      return;
    }

    const response = buildSyncResponse(conversation, frame);
    this.#sync.requestServed(response.records.length);
    this.diagnostics.bump("recordsSent", response.records.length);
    this.#replyTo(inbound, senderId, response);
  }

  async #onRecords(conversationId: string, records: SignedRecord[], senderId: string): Promise<void> {
    const conversation = this.#conversations.get(conversationId);
    if (!conversation) {
      this.diagnostics.warn("records", `dropped records for unknown conversation ${conversationId}`);
      return;
    }
    const summary = ingestBatch(conversation, records);
    this.diagnostics.bump("recordsReceived", summary.added.length);
    this.diagnostics.bump("duplicatesSuppressed", summary.duplicates);
    this.diagnostics.bump("recordsRejected", summary.rejected.length);
    for (const rejection of summary.rejected) {
      this.diagnostics.warn("records", `rejected ${rejection.messageId}: ${rejection.reason}`);
    }
    this.#sync.responseReceived(senderId, summary);

    if (summary.added.length > 0) {
      const state = conversation.state();
      for (const record of summary.added) {
        if (record.header.type !== "text") continue;
        const message = state.messages.find((entry) => entry.messageId === record.header.message_id);
        if (message) this.emit("message", message);
      }
      // Relay onward. This is what makes group chat work without every pair
      // being connected: whoever can reach a peer forwards to them.
      const relayable = summary.added.filter((record) => {
        const sender = state.participants.find((p) => p.peerId === record.header.sender_id);
        return sender?.admitted ?? false;
      });
      await this.#fanOut(conversation, relayable, senderId);
      this.emit("update");
    }
  }

  #replyTo(inbound: InboundFrame, peerId: string, frame: WireFrame): void {
    if (inbound.transport === "p2p" && inbound.connectionId) {
      this.#sendOnConnection(inbound.connectionId, frame);
      return;
    }
    const conversationId = "conversation_id" in frame ? String(frame.conversation_id) : null;
    const conversation = conversationId ? this.#conversations.get(conversationId) : undefined;
    const address = conversation
      ? this.#addressOf(conversation, peerId)
      : { peerId, hints: [] as TransportHint[] };
    void this.#send(address, frame);
  }

  #addressOf(conversation: Conversation, peerId: string): PeerAddress {
    const participant = conversation.state().participants.find((entry) => entry.peerId === peerId);
    return { peerId, hints: participant?.transports ?? [] };
  }

  // --- periodic work -----------------------------------------------------

  async syncAll(): Promise<void> {
    const deferredGap = this.#config.intervals?.deferredSyncMs ?? 60_000;
    const now = Date.now();
    for (const conversation of this.#conversations.values()) {
      for (const peer of this.peersOf(conversation)) {
        const route = this.#router.route(peer);
        if (route.state === "offline") continue;
        if (route.state === "smtp") {
          const last = this.#lastDeferredSync.get(peer.peerId) ?? 0;
          if (now - last < deferredGap) continue;
          this.#lastDeferredSync.set(peer.peerId, now);
        }
        this.#sync.requestSent(peer.peerId);
        await this.#send(peer, buildSyncRequest(conversation));
      }
    }
  }

  /** Re-dial peers we are not currently connected to. */
  async reconnectAll(): Promise<void> {
    if (!this.#p2p) return;
    for (const conversation of this.#conversations.values()) {
      for (const peer of this.peersOf(conversation)) {
        if (this.#p2p.isConnected(peer.peerId)) continue;
        if (!peer.hints.some((hint) => hint.kind === "p2p")) continue;
        try {
          const info = await this.#p2p.connectTo(peer);
          this.#sendHello(conversation.id, info.connectionId);
          this.diagnostics.info("p2p", `reconnected to ${peer.peerId}`);
        } catch {
          // Expected whenever the peer is off, asleep, or behind a NAT.
        }
      }
    }
  }

  // --- views -------------------------------------------------------------

  view(conversationId: string): ConversationView {
    const conversation = this.#require(conversationId);
    const state = conversation.state();
    const routes = this.peersOf(conversation).map((peer) => this.#router.route(peer));
    let invite: { link: string; uri: string } | null = null;
    try {
      invite = conversation.createInvite({});
    } catch {
      // No genesis record yet: we joined and are still syncing, so we cannot
      // mint invites for others. The UI shows this as "syncing".
    }
    return {
      conversationId,
      title: conversation.title,
      createdAt: conversation.meta.created_at,
      state,
      routes,
      connection: connectionBadge(routes),
      syncing: this.#sync.syncing,
      inviteLink: invite?.link ?? null,
      inviteUri: invite?.uri ?? null,
    };
  }

  views(): ConversationView[] {
    return this.conversations().map((conversation) => this.view(conversation.id));
  }

  status(): Record<string, unknown> {
    return {
      peerId: this.identity.peerId,
      displayName: this.identity.displayName,
      publicKey: toBase64Url(this.identity.publicKey),
      started: this.#started,
      hints: this.selfHints(),
      transports: this.#router.transports.map((transport) => transport.status()),
      connections: this.#p2p?.connections() ?? [],
      latency: Object.fromEntries(
        this.#p2p
          ? this.conversations().flatMap((conversation) =>
              this.peersOf(conversation).map((peer) => [peer.peerId, this.#p2p?.latencyTo(peer.peerId) ?? null]),
            )
          : [],
      ),
      sync: this.#sync.stats(),
      counters: this.diagnostics.counters(),
      outbox: this.#router.outboxSize,
      droppedFrames: this.#router.droppedCount,
      conversations: this.conversations().map((conversation) => ({
        id: conversation.id,
        title: conversation.title,
        records: conversation.log.size,
        conflicts: conversation.log.conflicts,
        watermarks: conversation.watermarks(),
      })),
    };
  }
}

function challengeBytes(challenge: string, peerId: string): Buffer {
  return canonicalBytes({ challenge, peer_id: peerId, purpose: "linkchat/1 handshake" });
}

function connectionBadge(routes: PeerRoute[]): "direct" | "smtp" | "local" | "offline" {
  if (routes.length === 0) return "offline";
  if (routes.some((route) => route.state === "direct")) return "direct";
  if (routes.some((route) => route.state === "local")) return "local";
  if (routes.some((route) => route.state === "smtp")) return "smtp";
  return "offline";
}
