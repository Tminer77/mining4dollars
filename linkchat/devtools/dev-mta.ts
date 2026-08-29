/**
 * A local mail transfer agent for development.
 *
 * This is a real SMTP server that accepts a real SMTP submission, spools the
 * message to disk, and relays it onward over real SMTP to the recipient's
 * node - retrying on a timer while the recipient is unreachable. That
 * retrying spool is the entire point: it is what lets Alice send to a Bob
 * whose laptop is shut, and what makes "SMTP as store-and-forward transport"
 * a demonstrable claim rather than a slogan.
 *
 * What it is NOT: a mail server. No DNS, no MX lookup, no spam filtering, no
 * DKIM/SPF/DMARC, no authentication, no TLS, no queue management to speak of,
 * bound to localhost. Do not put this on the internet. For real deployments
 * point LINKCHAT_SMTP_* at an actual submission service and let it do this
 * job properly - it already does.
 *
 * Run: node devtools/dev-mta.ts [--port 2525] [--shared .linkchat/dev]
 */
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createTransport } from "nodemailer";
import { SMTPServer } from "smtp-server";
import { readRoutes } from "./dev-mail.ts";

export type DevMtaOptions = {
  port?: number;
  host?: string;
  sharedDir: string;
  retryMs?: number;
  maxAttempts?: number;
  verbose?: boolean;
};

type QueueEntry = {
  id: string;
  to: string;
  from: string;
  attempts: number;
  nextAttemptAt: number;
  queuedAt: number;
};

export class DevMta {
  readonly #options: Required<DevMtaOptions>;
  readonly #spoolDir: string;
  readonly #queue = new Map<string, QueueEntry>();
  readonly #inFlight = new Set<string>();
  #flushing = false;
  #server: SMTPServer | null = null;
  #timer: NodeJS.Timeout | null = null;
  #port = 0;
  #accepted = 0;
  #delivered = 0;
  #failed = 0;

  constructor(options: DevMtaOptions) {
    this.#options = {
      port: options.port ?? 2525,
      host: options.host ?? "127.0.0.1",
      sharedDir: options.sharedDir,
      retryMs: options.retryMs ?? 2000,
      maxAttempts: options.maxAttempts ?? 200,
      verbose: options.verbose ?? false,
    };
    this.#spoolDir = join(this.#options.sharedDir, "spool");
    mkdirSync(this.#spoolDir, { recursive: true });
    this.#loadSpool();
  }

  get port(): number {
    return this.#port;
  }

  #loadSpool(): void {
    for (const name of readdirSync(this.#spoolDir)) {
      if (!name.endsWith(".json")) continue;
      try {
        const entry = JSON.parse(readFileSync(join(this.#spoolDir, name), "utf8")) as QueueEntry;
        this.#queue.set(entry.id, entry);
      } catch {
        continue;
      }
    }
  }

  async start(): Promise<void> {
    const server = new SMTPServer({
      authOptional: true,
      disabledCommands: ["AUTH", "STARTTLS"],
      onData: (stream, session, callback) => {
        const chunks: Buffer[] = [];
        stream.on("data", (chunk: Buffer) => chunks.push(chunk));
        stream.on("end", () => {
          const raw = Buffer.concat(chunks);
          for (const recipient of session.envelope.rcptTo) {
            this.#enqueue(raw, session.envelope.mailFrom ? session.envelope.mailFrom.address : "", recipient.address);
          }
          callback();
        });
      },
    });
    this.#server = server;
    await new Promise<void>((resolve, reject) => {
      server.listen(this.#options.port, this.#options.host, () => {
        const address = server.server.address();
        this.#port = typeof address === "object" && address ? address.port : this.#options.port;
        resolve();
      });
      server.once("error", reject);
    });
    this.#timer = setInterval(() => void this.flush(), this.#options.retryMs);
    this.#timer.unref();
    this.#log(`dev MTA listening on ${this.#options.host}:${this.#port}`);
    void this.flush();
  }

  async stop(): Promise<void> {
    if (this.#timer) clearInterval(this.#timer);
    this.#timer = null;
    const server = this.#server;
    this.#server = null;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  #enqueue(raw: Buffer, from: string, to: string): void {
    this.#accepted += 1;
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const entry: QueueEntry = {
      id,
      to,
      from,
      attempts: 0,
      nextAttemptAt: Date.now(),
      queuedAt: Date.now(),
    };
    writeFileSync(join(this.#spoolDir, `${id}.eml`), raw);
    writeFileSync(join(this.#spoolDir, `${id}.json`), JSON.stringify(entry));
    this.#queue.set(id, entry);
    this.#log(`accepted ${id} for ${to} (queue ${this.#queue.size})`);
    void this.flush();
  }

  /**
   * Attempt delivery of everything due. Safe to call at any time, including
   * concurrently: a second call while one is in progress returns immediately
   * rather than delivering the same spooled message twice.
   */
  async flush(): Promise<{ delivered: number; queued: number }> {
    if (this.#flushing) return { delivered: 0, queued: this.#queue.size };
    this.#flushing = true;
    try {
      return await this.#flushOnce();
    } finally {
      this.#flushing = false;
    }
  }

  async #flushOnce(): Promise<{ delivered: number; queued: number }> {
    const now = Date.now();
    const routes = readRoutes(this.#options.sharedDir);
    let delivered = 0;

    for (const entry of [...this.#queue.values()]) {
      if (entry.nextAttemptAt > now || this.#inFlight.has(entry.id)) continue;
      const route = routes.get(entry.to.toLowerCase());
      if (!route) {
        this.#defer(entry, "no route for recipient");
        continue;
      }
      const raw = readFileSync(join(this.#spoolDir, `${entry.id}.eml`));
      const transporter = createTransport({
        host: route.host,
        port: route.port,
        secure: false,
        ignoreTLS: true,
      });
      this.#inFlight.add(entry.id);
      try {
        await transporter.sendMail({
          envelope: { from: entry.from || "linkchat@localhost", to: entry.to },
          raw,
        });
        this.#dequeue(entry.id);
        delivered += 1;
        this.#delivered += 1;
        this.#log(`delivered ${entry.id} to ${entry.to} after ${entry.attempts} retries`);
      } catch (error) {
        this.#defer(entry, (error as Error).message);
      } finally {
        this.#inFlight.delete(entry.id);
        transporter.close();
      }
    }
    return { delivered, queued: this.#queue.size };
  }

  #defer(entry: QueueEntry, reason: string): void {
    entry.attempts += 1;
    if (entry.attempts > this.#options.maxAttempts) {
      this.#failed += 1;
      this.#dequeue(entry.id);
      this.#log(`gave up on ${entry.id}: ${reason}`);
      return;
    }
    // Real MTAs back off; this one stays brisk so a demo does not stall.
    entry.nextAttemptAt = Date.now() + Math.min(30_000, this.#options.retryMs * entry.attempts);
    writeFileSync(join(this.#spoolDir, `${entry.id}.json`), JSON.stringify(entry));
    this.#log(`holding ${entry.id} for ${entry.to}: ${reason} (attempt ${entry.attempts})`);
  }

  #dequeue(id: string): void {
    this.#queue.delete(id);
    for (const suffix of [".eml", ".json"]) {
      try {
        rmSync(join(this.#spoolDir, `${id}${suffix}`));
      } catch {
        // Already gone; nothing to do.
      }
    }
  }

  stats(): { accepted: number; delivered: number; failed: number; queued: number } {
    return {
      accepted: this.#accepted,
      delivered: this.#delivered,
      failed: this.#failed,
      queued: this.#queue.size,
    };
  }

  #log(message: string): void {
    if (this.#options.verbose) process.stdout.write(`[dev-mta] ${message}\n`);
  }
}

if (import.meta.filename === process.argv[1]) {
  const args = process.argv.slice(2);
  const valueOf = (flag: string, fallback: string): string => {
    const index = args.indexOf(flag);
    return index >= 0 && args[index + 1] ? String(args[index + 1]) : fallback;
  };
  const mta = new DevMta({
    port: Number(valueOf("--port", "2525")),
    sharedDir: valueOf("--shared", ".linkchat/dev"),
    verbose: true,
  });
  await mta.start();
  process.on("SIGINT", () => {
    void mta.stop().then(() => process.exit(0));
  });
}
