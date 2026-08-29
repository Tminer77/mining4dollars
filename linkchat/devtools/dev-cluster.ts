/**
 * One command that brings up the whole local experiment:
 *
 *   npm run dev
 *
 * a local MTA plus Alice, Bob and Carol, each a separate process with its own
 * identity, its own data directory, its own P2P listener and its own web UI.
 * Open the three URLs it prints in three browser windows and you have three
 * independent participants on one machine.
 *
 * Prefer real terminals? Run these side by side instead - same thing, one
 * process per window:
 *
 *   npm run dev:mta
 *   npm run dev:alice
 *   npm run dev:bob
 *   npm run dev:carol
 */
import { spawn, type ChildProcess } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { DevMta } from "./dev-mta.ts";

const here = dirname(fileURLToPath(import.meta.url));
const cli = join(here, "..", "src", "app", "cli.ts");
const shared = ".linkchat/dev";
const out = (line = ""): void => void process.stdout.write(`${line}\n`);

const mta = new DevMta({ port: 2525, sharedDir: shared, retryMs: 1500, verbose: true });
await mta.start();

const children: ChildProcess[] = [];
const profiles = [
  { name: "alice", ui: 7301 },
  { name: "bob", ui: 7302 },
  { name: "carol", ui: 7303 },
];

for (const profile of profiles) {
  const child = spawn(
    process.execPath,
    [
      cli,
      "start",
      "--profile",
      profile.name,
      "--dev-shared",
      shared,
      "--advertise",
      "127.0.0.1",
      "--ui-token",
      `dev-${profile.name}`,
      // Links minted here open straight into that node's own UI, which is
      // what makes "click the link" work on one machine. In a real
      // deployment this is a landing page that hands off to the local app.
      "--link-origin",
      `http://127.0.0.1:${profile.ui}`,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  children.push(child);
  const prefix = profile.name.padEnd(5);
  child.stdout?.on("data", (chunk: Buffer) => {
    for (const line of chunk.toString().split("\n")) {
      if (line.trim()) out(`${prefix} | ${line}`);
    }
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    for (const line of chunk.toString().split("\n")) {
      if (line.trim()) out(`${prefix} ! ${line}`);
    }
  });
}

await new Promise((resolve) => setTimeout(resolve, 1200));
out("");
out("  LinkChat dev cluster");
out("  --------------------");
for (const profile of profiles) {
  out(`  ${profile.name.padEnd(6)} http://127.0.0.1:${profile.ui}/?t=dev-${profile.name}`);
}
out(`  MTA    127.0.0.1:${mta.port}  (spool in ${shared}/spool)`);
out("");
out("  Try: in Alice's window, Create Conversation, copy the invite link,");
out("  paste it into Bob's Join box, then Carol's. Then stop Bob's process");
out("  (kill the process or close its port), send from Alice, and watch the");
out("  badge switch to SMTP and the MTA hold the message until Bob is back.");
out("");

const shutdown = async (): Promise<void> => {
  for (const child of children) child.kill("SIGTERM");
  await mta.stop();
  process.exit(0);
};
process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());
