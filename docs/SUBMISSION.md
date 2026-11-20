# Submission draft (update the numbers after the build)

## One-line pitch

Clawback is a personal agent that gets your money back after you buy, and can only act inside a Pact you signed.
The LLM proposes; a deterministic gate decides.

## Description (paste into the form)

Clawback gets your money back after you buy, and it can only act inside a Pact you signed. It reads order receipts
(`[through an Airbyte sync: keep only if it ran]`), stores everything in SingleStore, and matches each receipt to the current listing with
vector search (across stores, by UPC or model number). When a price drops inside the store's
window, it checks the store's real policy through Senso and proposes an action: a price-adjustment request, a claim packet
for stores that only take chat, a rebuy-and-return, or a deadline reminder. Every proposal passes a deterministic Pact gate
the user signed after verifying with Persona. The LLM proposes; code decides. Each decision shows the receipt, the price
found, the quoted policy and the rule that decided it. Built with `[only the sponsors that ran live: SingleStore, Senso, Persona, Arcee, Amplitude, Airbyte?]`. Simulated for the demo: the mock store's prices and support rep.

*(Edit the tools list to what actually shipped: if Arcee, SIE rerank or Firedrill were cut, remove them. Never list a sponsor
whose removal would not break something visible in the demo.)*

## Fields

| Field | Value |
|---|---|
| Repo | `[GitHub URL]` |
| Demo video | `[link, under 3 min]` |
| Team | Siva Surya Chandran, `[Teammate A]`, `[Teammate B]`, `[Teammate C]` |
| Emails | `[...]` |
| Screenshot | a "Stopped by your rules" card with its Why? panel open |
| Tools used | `[Amplitude (events + dashboard), SingleStore, Persona (sandbox), Arcee, Senso, Airbyte (read path) only if it ran]`, an LLM API for proposals, `[Firedrill if kept]` |

## Numbers to fill in at 4:00

- Dollars recovered in the demo run: `$___` (target: $34 claim + $40 lamp = $74)
- Unsafe actions stopped: `___` (target: 3 in the main demo)
- `pytest -q`: `___ passed` (whole suite; fill in at 4:00)
- Receipts parsed / average cost per receipt (Under the hood → Ingest): `___` / `$___`

## Honest scope (for the README and Q&A)

Matches each receipt to the current listing; cross-store identity by UPC or model number. Real stores never receive email;
they get a claim packet the user submits through the store's chat.
