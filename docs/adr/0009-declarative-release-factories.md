# 0009 — Releases are declared, planned, and preflighted

**Status:** Accepted · 2026-08-29

## Context

A release pipeline is the worst place in a project to learn something. Its
feedback loop is thirty to sixty minutes of runner time; its failures cluster at
the end, in the upload, where the cost of being wrong is highest; and the errors
that dominate are not interesting. A secret was never added. A build number was
already used. A scheme was renamed. `gradlew` lost its executable bit on a
Windows checkout.

Every one of those is knowable in under a second, locally, before anything
builds. What makes them expensive is that the pipeline is usually a shell script
in a YAML file: the knowledge is scattered across steps, nothing is checked
until the tool that needs it runs, and the only way to see what a release will
do is to run one.

The second problem is that a pipeline written for one app is a pipeline for one
app. Shipping a second one means copying the YAML and editing it, and the two
drift from the first divergence onward.

## Decision

Three separable pieces: a spec, a preflight, and a plan.

- **`factory.toml` is the whole release.** Bundle identifiers, project paths,
  schemes, modules, tracks. Nothing is passed as an argument that could be
  written down, so a release is reproducible from a file under version control.
  Parsing is **strict** — an unknown key is an error, because a silently ignored
  key is a setting the author believes is in force.
- **Preflight runs before anything builds**, and each check carries its own
  remedy. A check that cannot be answered in the current environment reports
  `SKIPPED`, never `PASSED`: a gate whose checks pass without running is not a
  gate. Validation that can be permanent is strictest — a bundle identifier
  cannot be changed once a store record exists under it, so a permissive check
  buys a permanent mistake.
- **A plan is data, not execution.** The commands can be printed, read, and
  diffed before a runner is spent on them, which matters because the costly
  errors are visible in what the commands *say*.
- **Build numbers are decided and checked up front.** Both stores reject a
  repeated or lower build number, and both reject it at upload.
- **Every captured line is scrubbed of the credentials the step was given.**
  Some tools echo their environment, and CI logs are the first thing anyone
  reads when a build fails.
- **The factory is project-agnostic.** One implementation, one set of tests, any
  number of apps.

## Consequences

The expensive failures move from the end of a runner's hour to the start of a
developer's second, and the answer arrives with its fix attached. Two apps share
one implementation, so a fix to signing is a fix for both.

The costs are real. A spec is another file to keep in step with the project, and
strict parsing means a renamed key fails a release rather than being ignored —
deliberate, but it will be inconvenient at least once. Preflight can only check
what is checkable locally: it cannot tell you a provisioning profile has expired
or that App Review will object to the app, so it narrows the failure surface
without closing it. And the plan is only as right as the spec; a scheme that
exists but builds the wrong target passes every check here.

The signing and upload leg is the one part that cannot be verified in the
environment that produced it. It needs one real run on a macOS runner to settle,
and until it has had one it should be treated as unproven.

## Alternatives considered

**A shell script per app in the workflow YAML.** What almost everyone does,
and it has a real advantage: nothing to learn, and the whole release is visible
in one file. Rejected because it makes every check implicit in the tool that
fails on it, gives no way to see a release without running one, and multiplies
per app.

**fastlane.** Mature, far more capable than this, and the obvious answer for a
team already using it. Rejected for now because it moves the configuration into
Ruby and a Fastfile without adding the preflight that this exists for — the
expensive failures would still be discovered by running. Worth revisiting if the
matrix grows beyond two platforms; the plan is deliberately a list of commands
so it could emit fastlane lanes instead of invoking tools directly.

**Checked-in `ExportOptions.plist`.** One less generated file. Rejected because
every value in it already lives in the spec, and a second copy is one more thing
that drifts from the first.

**Validating nothing and letting the tools speak.** Zero code, and the tools'
errors are authoritative. Rejected because "authoritative" arrives an hour late,
and `xcodebuild`'s account of a missing secret is not the sentence anyone needs.
