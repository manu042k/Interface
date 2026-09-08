# Computer-Use Automation System for Legacy Banking Applications

Backend integration layer that gives AI agents the ability to operate API-less
back-office banking / credit-union applications, via **LLM-driven discovery** +
**deterministic replay**.

The system's one job: turn *"an LLM figured out how to do X once"* into *"any agent
can reliably invoke X a thousand times, cheaply, without a model in the loop, and a
human can always take the wheel when it can't."*

## Documents

| Doc | Purpose |
|---|---|
| [`TDD-ComputerUse-Automation-System.md`](./TDD-ComputerUse-Automation-System.md) | Technical design: domains, architecture, failure modes, ADRs, data models |

## Domains

- **Discovery** — LLM observe → decide → act loop against a live surface; produces artifacts.
- **Capability** — owns the versioned, reviewable `CapabilityArtifact` contract.
- **Execution** — deterministic replay: locator resolution, replay executor, checkpoints.
- **Human-Loop** — stuck detection, intervention requests, control-transfer / session handoff.
- **Platform / Cross-Cutting** — surface adapters, policy/guardrail engine, secrets & redaction, observability.

## Status

Design phase. Implementation not yet started.
