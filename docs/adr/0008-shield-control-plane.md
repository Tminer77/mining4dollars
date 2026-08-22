# 0008 — Shield as the first product domain

**Status:** Accepted · 2026-08-22

## Context

The foundation shipped an append-only event log and a four-layer architecture,
deliberately without the platform's own entities. The product to put on that
base is a company control plane for antivirus and machine optimizers: enrol the
fleet, ingest detections, isolate compromised boxes, and propose the next
change.

Two shapes were plausible. A library of signatures that runs on each box, with
this service as a thin log. Or a control plane that owns inventory, policy, and
optimizer decisions, with agents as reporters.

## Decision

This service is the control plane. Agents scan; Shield decides.

- **Inventory** is first-class. Hostname is the natural key. Heartbeats never
  lift quarantine.
- **Classification** is deterministic company policy living in the domain
  (`classify_finding`). High-confidence malware auto-quarantines in the same
  transaction as the finding. A later model adapter can wrap the function; it
  cannot replace the rules, because "EICAR is malware and we isolate it" is
  not a prediction.
- **Optimizer plans** are composed from role + open findings
  (`propose_plan`). Security outranks performance. Performance and thermal
  plans cannot be applied to an isolated box.
- **Single-tenant.** One company per deployment. Tenancy is not a column we
  would later migrate onto every table without pain; it is a deployment
  topology we can change when there is a second company.

The event log remains the activity record. Shield writes to it inside the same
unit of work as the state change it describes.

## Consequences

Operators get one API, one error contract, and one transaction around
"this file is malware, isolate the box, record why". Agents stay simple.
Classification is unit-testable without a model or a database.

The cost is that this process does not itself examine files. An agent that
cannot reach the control plane can still scan locally but cannot isolate via
policy, and an operator looking only at the box cannot see fleet state.

Putting a real model behind the classifier later is an adapter change, not a
rewrite of scans, findings, or quarantine.

## Alternatives considered

**On-box only, this service as a log.** Much less to persist, and genuinely
the right answer for a consumer AV. Rejected because the company product is
the *fleet* decision: isolate this miner, do not raise clocks on it, propose
recovery for the rack.

**Classification as an HTTP call to a model on every ingest.** Would look more
like "AI" in a demo. Rejected as the source of truth: a model outage would
stop quarantine, and an operator could not read a stable rationale. Policy
runs always; a model, if added, may only raise confidence, never lower a
mandatory isolation rule.

**Multi-tenant from day one (`company_id` on every table).** Correct for a
SaaS. Rejected because there is one company and one deployment. Adding the
column everywhere now would tax every query for a constraint we do not have.
