"""Shield: company AI antivirus and machine optimizers.

This is the first product domain on the mining4dollars foundation. The control
plane does not scan files itself. Agents on company machines do that; this
service is where those machines are enrolled, where detections become policy
decisions, and where optimizer plans are composed.

Single-tenant: one company per deployment. Multi-tenant isolation is a later
change, not a missing column.

## Why a control plane

An antivirus that lives only on the box cannot isolate the box, cannot see the
fleet, and cannot tell a miner from a gateway. An optimizer that lives only on
the box will raise clocks on a machine that should be dark. Both decisions
belong here, next to the activity log, behind the same error contract and the
same transaction boundary.

## The pieces

**Endpoints.** Inventory. Hostname is the natural key; a re-register refreshes
last-seen rather than duplicating the machine. Heartbeats never lift quarantine
— isolation is an operator decision, not something an infected agent talks its
way out of.

**Scans and findings.** The agent queues a scan, marks it running, streams
findings, and completes. `findings_count` is the control plane's tally, so a
compromised agent cannot under-report detections.

**Classification.** Deterministic company policy, not a model call. Signature
rules (EICAR, `family:`, `hash:`, `cve-`, `cfg:`) beat the agent's claimed
category. High-confidence malware auto-quarantines the endpoint. The rationale
string is what an operator reads when they ask *why*. A later model can sit in
front of the same function; the rules still have to hold.

**Optimizer plans.** Composed from the endpoint's role and its still-open
findings. Security outranks performance: a miner with malware gets signature
refresh and a full rescan, not a power-limit bump. Performance and thermal
plans cannot be applied while the box is isolated.

## The path a detection takes

1. `POST /v1/endpoints` — enrol the machine.
2. `POST /v1/endpoints/{id}/scans` — queue work for the agent.
3. `POST /v1/scans/{id}/start` — agent is examining the box.
4. `POST /v1/scans/{id}/findings` — each detection is classified; malware at
   ≥ 0.90 confidence isolates the endpoint in the same transaction.
5. `POST /v1/scans/{id}/complete` — agent is done; the tally is ours.
6. `POST /v1/endpoints/{id}/optimizer/plans` — propose recovery or a tune.
7. `POST /v1/optimizer/plans/{id}/apply` — or accept / reject first.

Every step appends a `shield.*` event to the system log, so the activity record
already in this repository is how operators reconstruct an incident.
