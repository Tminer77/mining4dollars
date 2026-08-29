/**
 * Dev-only address routing.
 *
 * On the real internet, "where does mail for bob@example.com go?" is answered
 * by DNS MX records. There is no DNS in a laptop demo, so each node drops a
 * small route file naming the port its SMTP listener is on, and the dev MTA
 * reads them. This stands in for MX lookup and for nothing else - it is not
 * part of the protocol, and it is not used when the relay is a real provider.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export type DevRoute = { address: string; host: string; port: number; updatedAt: string };

export function routesDir(sharedDir: string): string {
  const dir = join(sharedDir, "routes");
  mkdirSync(dir, { recursive: true });
  return dir;
}

function fileFor(dir: string, address: string): string {
  return join(dir, `${address.replace(/[^a-z0-9._@-]/gi, "_")}.json`);
}

export function registerRoute(sharedDir: string, route: Omit<DevRoute, "updatedAt">): void {
  const dir = routesDir(sharedDir);
  const payload: DevRoute = { ...route, updatedAt: new Date().toISOString() };
  writeFileSync(fileFor(dir, route.address), `${JSON.stringify(payload, null, 2)}\n`);
}

export function readRoutes(sharedDir: string): Map<string, DevRoute> {
  const dir = routesDir(sharedDir);
  const out = new Map<string, DevRoute>();
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    try {
      const route = JSON.parse(readFileSync(join(dir, name), "utf8")) as DevRoute;
      out.set(route.address.toLowerCase(), route);
    } catch {
      continue;
    }
  }
  return out;
}
