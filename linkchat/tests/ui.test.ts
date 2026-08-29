/**
 * The local UI is the one component that is not part of the protocol, so what
 * matters here is that it cannot be driven by anyone who does not hold the
 * token, and that it reflects node state faithfully.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { WebSocket } from "ws";
import { UiServer } from "../src/ui/server.ts";
import { LocalBus } from "../src/transports/local/local-transport.ts";
import { localNode, until } from "./helpers.ts";

test("the UI refuses HTTP and WebSocket access without the token", async (t) => {
  const node = localNode("Alice", new LocalBus());
  const ui = new UiServer({ node, port: 0, token: "secret-token" });
  t.after(async () => {
    await ui.stop();
    await node.stop();
  });
  await node.start();
  await ui.start();

  const base = `http://127.0.0.1:${ui.port}`;
  assert.equal((await fetch(`${base}/`)).status, 401);
  assert.equal((await fetch(`${base}/?t=wrong`)).status, 401);
  assert.equal((await fetch(`${base}/api/status?t=nope`)).status, 401);
  assert.equal((await fetch(`${base}/?t=secret-token`)).status, 200);

  const refused = await new Promise<string>((resolve) => {
    const socket = new WebSocket(`ws://127.0.0.1:${ui.port}/?t=wrong`);
    socket.on("error", (error) => resolve(error.message));
    socket.on("open", () => resolve("opened"));
  });
  assert.match(refused, /401/);
});

test("the UI pushes state and accepts commands over the local socket", async (t) => {
  const bus = new LocalBus();
  const alice = localNode("Alice", bus);
  const bob = localNode("Bob", bus);
  const ui = new UiServer({ node: alice, port: 0, token: "tok" });
  t.after(async () => {
    await ui.stop();
    await Promise.all([alice.stop(), bob.stop()]);
  });
  await alice.start();
  await bob.start();
  await ui.start();

  const socket = new WebSocket(`ws://127.0.0.1:${ui.port}/?t=tok`);
  const frames: Record<string, unknown>[] = [];
  socket.on("message", (raw) => frames.push(JSON.parse(raw.toString())));
  await new Promise((resolve) => socket.on("open", resolve));

  await until(() => frames.length > 0, 4000, "initial state push");
  const first = frames[0] as { type: string; self: { displayName: string } };
  assert.equal(first.type, "state");
  assert.equal(first.self.displayName, "Alice");

  socket.send(JSON.stringify({ type: "create", title: "From the UI" }));
  await until(() => alice.conversations().length === 1, 4000, "conversation created");
  const conversationId = alice.conversations()[0]!.id;

  socket.send(JSON.stringify({ type: "invite", conversationId }));
  await until(() => frames.some((frame) => frame.type === "invite"), 4000, "invite minted");
  const invite = frames.find((frame) => frame.type === "invite") as { link: string };
  assert.match(invite.link, /\/join\/c_/);

  await bob.joinByLink(invite.link);
  socket.send(JSON.stringify({ type: "send", conversationId, text: "typed in a browser" }));
  await until(
    () => bob.view(bob.conversations()[0]!.id).state.messages.some((m) => m.text === "typed in a browser"),
    6000,
    "message delivered to Bob",
  );

  socket.send(JSON.stringify({ type: "send", conversationId, text: "" }));
  await until(() => frames.some((frame) => frame.type === "error"), 4000, "errors are reported back");

  const status = await (await fetch(`http://127.0.0.1:${ui.port}/api/status?t=tok`)).json();
  assert.equal(status.peerId, alice.identity.peerId);
  socket.close();
});

test("the join route serves the app so a clicked link can be handed to the node", async (t) => {
  const node = localNode("Alice", new LocalBus());
  const ui = new UiServer({ node, port: 0, token: "tok" });
  t.after(async () => {
    await ui.stop();
    await node.stop();
  });
  await node.start();
  await ui.start();

  const response = await fetch(`http://127.0.0.1:${ui.port}/join/c_0123456789ABCDEFGHJKMNPQRS`);
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /LinkChat/);
  // The page must read the fragment itself; the server never sees it.
  assert.match(html, /location\.hash/);
});
