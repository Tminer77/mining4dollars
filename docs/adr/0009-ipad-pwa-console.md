# 0009 — iPad console is a same-origin PWA

**Status:** Accepted · 2026-08-22

## Context

Operators asked for iPad software that stays on the device. This repository
runs in environments that do not have Xcode, Apple signing certificates, or
the App Store pipeline, so a native UIKit/SwiftUI binary cannot be produced
or verified here.

The HTTP API already exists. What was missing was a product surface an iPad
can keep on the home screen.

## Decision

Ship the console as a Progressive Web App, same origin as the API, installable
with Safari's Add to Home Screen.

- `display: standalone` so it opens without Safari chrome.
- A service worker caches the shell so the UI remains on the iPad when the
  network drops; `/v1`, `/healthz`, and `/readyz` are never cached.
- IndexedDB on the device is the source of truth the operator sees. New
  events are written there first and posted to the API when it answers.
  The server is a replica, not a requirement for using the console.
- Asset URLs are relative so the same files can be served by FastAPI at
  ``/`` and by GitHub Pages at ``https://tminer77.github.io/mining4dollars/``.
  That HTTPS origin is what lets Safari Add to Home Screen from an iPad
  that cannot reach a local process.
- The console is a delivery surface. It calls the public HTTP API. It does
  not import domain or database code.

## Consequences

The app can be developed, served, and tested on Linux. An iPad user can
install it in three taps and keep it next to every other app.

Native capabilities that require a signed IPA (push via APNs, background
fetch, on-device Keychain, App Store distribution) are not available. If
those become required, a Swift client can consume the same API; this
decision does not block that. It rejects making the first iPad client
depend on a Mac.

## Alternatives considered

**SwiftUI iPad app.** The right long-term shape for a first-party Apple
client. Rejected as the first slice because this environment cannot compile
or sign it, so it would be source we cannot run.

**Separate SPA on another origin.** Familiar, and lets the UI deploy
independently. Rejected because it forces CORS, a second hosting unit, and
a more hostile Add to Home Screen story. Same-origin is simpler and safer.

**Capacitor / wrapper around a web view, distributed as an IPA.** Gets an
icon on the home screen via the App Store. Rejected for the same signing
and toolchain reasons as SwiftUI, with extra moving parts.
