# Clawback: sponsor pitches (copy/paste into the submission form)

Everything below ran live in our demo. Anything simulated is labelled as such.

**Project one-liner:** Clawback is an AI agent that gets your money back after you buy. It can only act inside a Pact you signed, and a fixed rule gate, not the model, decides.

---

## Senso: Best use of Senso
**Hook:** Most agents *guess* a store's return policy. Ours quotes it.

Clawback ingests each store's policy into Senso once. Whenever the agent considers a claim, it asks Senso what the policy says and attaches the exact passage and version ID to the decision. Open the "Why?" panel on any decision and you see the store's own sentence, retrieved through Senso, next to the rule that fired. If Senso finds no source, the gate refuses to act. Senso gives our agent evidence it can cite, not vibes. Without it, "the policy says so" would just be a claim we made up.

## SingleStore: Best use of SingleStore
**Hook:** Every decision, every signature, every dollar: one database you can audit.

SingleStore Helios is Clawback's system of record: orders, line items, policies, signed Pacts, insert-only revocations, proposals, approvals, the run-once guard, and the audit log of every gate decision. The run-once guard uses a conditional update, so a repeated tap can never pay out twice. We built a per-thread connection layer and retry logic on top of Helios, so the UI stays quick even over TLS from a laptop. If you want to see why an agent did something, you query a table.

## Amplitude: Best use of Amplitude
**Hook:** We measure the agent the way you'd measure a product.

Every gate decision emits an Amplitude event with outcome (ALLOW / ASK / BLOCK / NO_ACTION), the rule that decided, and dollars recovered. Our dashboards show the decision mix, stop reasons by rule, and total money recovered for the run ($74 in the demo). Tracking *why an agent refused* is as important as tracking what it did. Amplitude made that visible in minutes.

## Airbyte: Best use of Airbyte
**Hook:** Receipts in, structured purchases out, no glue scripts.

We ran Airbyte Core (abctl) locally, with a File source feeding a Postgres destination. Receipt data syncs into the database, and the agent's ingest adapter reads the result. That is the real sync, run live. (The Gmail link is simulated for the demo, and the README says so.) Airbyte turns "my purchases live in a pile of emails" into a table the agent can reason over.

## Open-weight model (NVIDIA NIM): Best use of open models
**Hook:** The model proposes. It never decides.

The agent runs on an open-weight model (NVIDIA Nemotron 3.5 Lightning, via NVIDIA's OpenAI-compatible endpoint). It reads receipts and writes proposals. The model has no authority: its output goes through a deterministic gate (rules R0 to R13), checked against a signed Pact. A small, cheap open model is enough because we don't ask it to be trustworthy. We built the system so it doesn't have to be.

---

## Optional: Persona (say it honestly)
Only submit this one if the form asks about integrations built, not just live ones.

**Hook:** Approving money movement should need a verified human.

We built the Persona flow (inquiry creation, webhook signature verification, and an identity re-check before every approval), and tested it offline. **In the demo, Persona is simulated** (a seeded "approved" status), because we did not get live dashboard access in time. Do not claim it as live.

---

## 20-second judge pitch
> Agents that spend your money are scary. Clawback makes that safe: you sign a Pact, the AI proposes, a fixed gate decides, and every decision is signed, quoted from the store's own policy through Senso, stored in SingleStore, and measured in Amplitude. Fed by Airbyte, run by an open model. It stopped the wrong-colour backpack, asked before the lamp, and got $34 back on the earbuds. Pick us because it's the one that knows when to say no.
