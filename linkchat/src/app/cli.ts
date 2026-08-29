#!/usr/bin/env node
/**
 * LinkChat command line.
 *
 *   node src/app/cli.ts start --profile alice     one of the three dev identities
 *   node src/app/cli.ts start --name Dave --tty   a node with a terminal chat
 *   node src/app/cli.ts identity --profile alice  show this device's identity
 *   node src/app/cli.ts link <invite-link>        inspect a link without joining
 */
import { createInterface } from "node:readline";
import { LinkChatNode, type NodeConfig } from "../node/node.ts";
import { Keystore } from "../identity/keystore.ts";
import { parseInviteLink } from "../protocol/invite.ts";
import { MaildirSource, SmtpListenerSource } from "../transports/smtp/inbound.ts";
import { UiServer } from "../ui/server.ts";
import { registerRoute } from "../../devtools/dev-mail.ts";
import { parseArgs, resolveConfig, type ResolvedConfig } from "./config.ts";

const out = (line = ""): void => void process.stdout.write(`${line}\n`);

async function buildNode(config: ResolvedConfig): Promise<{
  node: LinkChatNode;
  afterStart: () => void;
}> {
  let inbound: SmtpListenerSource | MaildirSource | undefined;
  if (config.smtp.enabled) {
    if (config.smtp.maildir) {
      inbound = new MaildirSource({ path: config.smtp.maildir });
    } else if (config.smtp.listenPort !== null) {
      inbound = new SmtpListenerSource({
        port: config.smtp.listenPort,
        host: "127.0.0.1",
        addresses: [config.smtp.address],
      });
    }
  }

  const nodeConfig: NodeConfig = {
    displayName: config.displayName,
    ...(config.ephemeral ? { ephemeral: true } : { dataDir: config.dataDir }),
    ...(config.keyPassphrase ? { keyPassphrase: config.keyPassphrase } : {}),
    linkOrigin: config.linkOrigin,
    p2p: config.p2p.enabled
      ? {
          host: config.p2p.host,
          port: config.p2p.port,
          ...(config.p2p.advertiseHost ? { advertiseHost: config.p2p.advertiseHost } : {}),
        }
      : false,
    smtp: config.smtp.enabled
      ? {
          address: config.smtp.address,
          relay: config.smtp.relay,
          ...(inbound ? { inbound } : {}),
        }
      : false,
  };

  const node = new LinkChatNode(nodeConfig);
  return {
    node,
    afterStart: () => {
      // Dev only: tell the local MTA which port this node listens on. On the
      // real internet this is what DNS MX records do.
      if (
        config.smtp.enabled &&
        config.smtp.devSharedDir &&
        inbound instanceof SmtpListenerSource &&
        inbound.port > 0
      ) {
        registerRoute(config.smtp.devSharedDir, {
          address: config.smtp.address,
          host: "127.0.0.1",
          port: inbound.port,
        });
      }
    },
  };
}

async function start(config: ResolvedConfig): Promise<void> {
  const { node, afterStart } = await buildNode(config);
  await node.start();
  afterStart();

  out("");
  out(`  LinkChat  ${node.identity.displayName}`);
  out(`  peer id   ${node.identity.peerId}`);
  for (const hint of node.selfHints()) {
    out(`  ${hint.kind.padEnd(9)} ${hint.kind === "p2p" ? hint.url : hint.address}`);
  }
  if (!config.smtp.enabled) out("  smtp      not configured (P2P only)");

  let ui: UiServer | null = null;
  if (config.ui.enabled) {
    ui = new UiServer({
      node,
      port: config.ui.port,
      host: config.ui.host,
      ...(config.ui.token ? { token: config.ui.token } : {}),
    });
    await ui.start();
    out(`  ui        ${ui.url}`);
  }
  out("");

  if (config.tty) runTty(node);

  const shutdown = async (): Promise<void> => {
    await ui?.stop();
    await node.stop();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());
}

/** A terminal chat client, so a developer can run three of these side by side. */
function runTty(node: LinkChatNode): void {
  const rl = createInterface({ input: process.stdin, output: process.stdout, prompt: "> " });
  let current: string | null = node.conversations()[0]?.id ?? null;

  const help = [
    "  /create [title]   create a conversation and print its invite link",
    "  /join <link>      join a conversation from an invite link",
    "  /link             print the current invite link",
    "  /who              list participants and how each is reachable",
    "  /status           transport and sync diagnostics",
    "  /use <n>          switch conversation",
    "  /quit             exit",
    "  anything else     send a message",
  ].join("\n");
  out(help);
  rl.prompt();

  node.on("message", (message: { senderName: string; text: string; mine: boolean }) => {
    if (message.mine) return;
    out(`\n${message.senderName}: ${message.text}`);
    rl.prompt();
  });

  rl.on("line", (line) => {
    const input = line.trim();
    void (async () => {
      try {
        if (input.startsWith("/create")) {
          const title = input.slice("/create".length).trim() || "Conversation";
          const { conversation, link } = node.createConversation(title);
          current = conversation.id;
          out(`created ${conversation.id}`);
          out(`invite link: ${link}`);
        } else if (input.startsWith("/join ")) {
          const conversation = await node.joinByLink(input.slice(6).trim());
          current = conversation.id;
          out(`joining ${conversation.id} - history will arrive as peers answer`);
        } else if (input === "/link") {
          if (!current) out("no conversation selected");
          else out(node.createInvite(current).link);
        } else if (input === "/who") {
          if (!current) out("no conversation selected");
          else {
            const view = node.view(current);
            for (const peer of view.state.participants) {
              const route = view.routes.find((entry) => entry.peerId === peer.peerId);
              const how = peer.isSelf ? "you" : (route?.state ?? "offline");
              out(`  ${peer.displayName.padEnd(12)} ${peer.peerId}  ${how}${peer.admitted ? "" : "  NOT ADMITTED"}`);
            }
            out(`  transport: ${view.connection}${view.syncing ? " (synchronizing)" : ""}`);
          }
        } else if (input === "/status") {
          out(JSON.stringify(node.status(), null, 2));
        } else if (input.startsWith("/use ")) {
          const index = Number(input.slice(5).trim()) - 1;
          const conversation = node.conversations()[index];
          if (conversation) {
            current = conversation.id;
            out(`using ${conversation.title}`);
          } else out("no such conversation");
        } else if (input === "/quit" || input === "/exit") {
          await node.stop();
          process.exit(0);
        } else if (input === "/help") {
          out(help);
        } else if (input.length > 0) {
          if (!current) out("no conversation yet - use /create or /join");
          else await node.post(current, input);
        }
      } catch (error) {
        out(`error: ${(error as Error).message}`);
      }
      rl.prompt();
    })();
  });
}

function identity(config: ResolvedConfig): void {
  const keystore = new Keystore(config.dataDir, config.keyPassphrase);
  if (!keystore.exists()) {
    out(`no identity at ${keystore.path} yet; it is created on first start`);
    return;
  }
  const loaded = keystore.load();
  out(`  peer id      ${loaded.peerId}`);
  out(`  display name ${loaded.displayName}`);
  out(`  key file     ${keystore.path}`);
  out(
    keystore.isPassphraseProtected()
      ? "  protection   scrypt + AES-256-GCM (LINKCHAT_KEY_PASSPHRASE)"
      : "  protection   NONE - the private key is stored in the clear (mode 0600).\n" +
        "               Set LINKCHAT_KEY_PASSPHRASE before first start to wrap it.",
  );
}

function inspectLink(link: string): void {
  const parsed = parseInviteLink(link);
  out(`  conversation ${parsed.conversationId}`);
  out(`  invited by   ${parsed.invitedBy ?? "(not stated)"}`);
  out(`  invite nonce ${parsed.invite.nonce}`);
  out(`  expires      ${new Date(parsed.invite.exp).toISOString()}`);
  out(`  key          ${parsed.conversationKey.length} bytes (present in the fragment)`);
  for (const hint of parsed.hints) {
    out(`  reach via    ${hint.kind}: ${hint.kind === "p2p" ? hint.url : hint.address}`);
  }
}

async function main(): Promise<void> {
  const { command, args } = parseArgs(process.argv.slice(2));
  if (args.has("help") || command === "help") {
    out("usage: linkchat <start|identity|link> [options]");
    out("  --profile alice|bob|carol   dev identity with fixed ports");
    out("  --name <name>               display name");
    out("  --data <dir>                data directory");
    out("  --tty                       terminal chat client");
    out("  --ui-port <n> --p2p-port <n> --advertise <host> --link-origin <url>");
    out("  --no-p2p --no-smtp --no-ui --ephemeral");
    return;
  }

  const config = resolveConfig(args);
  switch (command) {
    case "start":
      await start(config);
      break;
    case "identity":
      identity(config);
      break;
    case "link": {
      const link = process.argv.slice(3).find((token) => token.includes("/join/"));
      if (!link) {
        out("usage: linkchat link <invite-link>");
        process.exitCode = 1;
        return;
      }
      inspectLink(link);
      break;
    }
    default:
      out(`unknown command '${command}' - try 'help'`);
      process.exitCode = 1;
  }
}

await main();
