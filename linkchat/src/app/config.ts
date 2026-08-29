/**
 * Configuration: command line first, then environment, then defaults.
 *
 * Credentials are read from the environment only. Nothing here writes a
 * password to disk, puts one in a link, or logs one.
 */
import { join } from "node:path";
import type { SmtpRelayConfig } from "../transports/smtp/smtp-transport.ts";
import { relayFromEnv } from "../transports/smtp/smtp-transport.ts";

export type Profile = {
  name: string;
  displayName: string;
  dataDir: string;
  uiPort: number;
  p2pPort: number;
  smtpAddress: string;
  smtpListenPort: number;
};

/**
 * Fixed ports for the three demo identities so that a developer can restart
 * one terminal without every published address going stale.
 */
export const DEV_PROFILES: Record<string, Profile> = {
  alice: {
    name: "alice",
    displayName: "Alice",
    dataDir: ".linkchat/alice",
    uiPort: 7301,
    p2pPort: 7401,
    smtpAddress: "alice@linkchat.test",
    smtpListenPort: 7501,
  },
  bob: {
    name: "bob",
    displayName: "Bob",
    dataDir: ".linkchat/bob",
    uiPort: 7302,
    p2pPort: 7402,
    smtpAddress: "bob@linkchat.test",
    smtpListenPort: 7502,
  },
  carol: {
    name: "carol",
    displayName: "Carol",
    dataDir: ".linkchat/carol",
    uiPort: 7303,
    p2pPort: 7403,
    smtpAddress: "carol@linkchat.test",
    smtpListenPort: 7503,
  },
};

export type ResolvedConfig = {
  displayName: string;
  dataDir: string;
  ephemeral: boolean;
  keyPassphrase: string | undefined;
  linkOrigin: string;
  ui: { enabled: boolean; port: number; host: string; token?: string };
  p2p: { enabled: boolean; port: number; host: string; advertiseHost?: string };
  smtp:
    | {
        enabled: true;
        address: string;
        relay: SmtpRelayConfig;
        listenPort: number | null;
        maildir: string | null;
        devSharedDir: string | null;
      }
    | { enabled: false };
  tty: boolean;
};

export type Args = Map<string, string | boolean>;

export function parseArgs(argv: string[]): { command: string; args: Args } {
  const args: Args = new Map();
  const positional: string[] = [];
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index] ?? "";
    if (!token.startsWith("--")) {
      positional.push(token);
      continue;
    }
    const key = token.slice(2);
    const next = argv[index + 1];
    if (next === undefined || next.startsWith("--")) {
      args.set(key, true);
    } else {
      args.set(key, next);
      index += 1;
    }
  }
  return { command: positional[0] ?? "start", args };
}

const str = (args: Args, key: string): string | undefined => {
  const value = args.get(key);
  return typeof value === "string" ? value : undefined;
};
const flag = (args: Args, key: string): boolean => args.get(key) === true || args.get(key) === "true";
const num = (value: string | undefined, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export function resolveConfig(args: Args, env: NodeJS.ProcessEnv = process.env): ResolvedConfig {
  const profileName = str(args, "profile");
  const profile = profileName ? DEV_PROFILES[profileName.toLowerCase()] : undefined;
  if (profileName && !profile) {
    throw new Error(`unknown profile '${profileName}' (try alice, bob or carol)`);
  }

  const devShared = str(args, "dev-shared") ?? env.LINKCHAT_DEV_SHARED ?? (profile ? ".linkchat/dev" : undefined);
  const displayName =
    str(args, "name") ?? env.LINKCHAT_DISPLAY_NAME ?? profile?.displayName ?? "Anonymous";
  const dataDir = str(args, "data") ?? env.LINKCHAT_DATA_DIR ?? profile?.dataDir ?? ".linkchat/default";

  // A dev profile implies the local MTA; a real deployment must configure a
  // relay explicitly, and gets none by default.
  let relay = relayFromEnv(env);
  const smtpAddress = str(args, "smtp-address") ?? env.LINKCHAT_SMTP_ADDRESS ?? profile?.smtpAddress;
  if (!relay && profile && devShared) {
    relay = {
      host: "127.0.0.1",
      port: num(env.LINKCHAT_SMTP_PORT ?? str(args, "mta-port"), 2525),
      secure: false,
      from: profile.smtpAddress,
      allowInsecure: true,
    };
  }

  const smtpEnabled = Boolean(relay && smtpAddress) && !flag(args, "no-smtp");
  const listenPort = num(
    str(args, "smtp-listen") ?? env.LINKCHAT_SMTP_LISTEN_PORT,
    profile?.smtpListenPort ?? 0,
  );

  return {
    displayName,
    dataDir,
    ephemeral: flag(args, "ephemeral"),
    keyPassphrase: env.LINKCHAT_KEY_PASSPHRASE,
    linkOrigin: str(args, "link-origin") ?? env.LINKCHAT_LINK_ORIGIN ?? "https://linkchat.local",
    ui: {
      enabled: !flag(args, "no-ui"),
      port: num(str(args, "ui-port") ?? env.LINKCHAT_UI_PORT, profile?.uiPort ?? 0),
      host: str(args, "ui-host") ?? "127.0.0.1",
      ...(str(args, "ui-token") ? { token: str(args, "ui-token") as string } : {}),
    },
    p2p: {
      enabled: !flag(args, "no-p2p"),
      port: num(str(args, "p2p-port") ?? env.LINKCHAT_P2P_PORT, profile?.p2pPort ?? 0),
      host: str(args, "p2p-host") ?? "0.0.0.0",
      ...(str(args, "advertise") ?? env.LINKCHAT_ADVERTISE_HOST
        ? { advertiseHost: (str(args, "advertise") ?? env.LINKCHAT_ADVERTISE_HOST) as string }
        : {}),
    },
    smtp:
      smtpEnabled && relay && smtpAddress
        ? {
            enabled: true,
            address: smtpAddress,
            relay,
            listenPort: env.LINKCHAT_MAILDIR ? null : listenPort,
            maildir: env.LINKCHAT_MAILDIR ?? null,
            devSharedDir: devShared ?? null,
          }
        : { enabled: false },
    tty: flag(args, "tty"),
  };
}

export function dataDirFor(root: string, profile: string): string {
  return join(root, profile);
}
