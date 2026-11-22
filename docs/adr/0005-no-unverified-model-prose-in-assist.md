# No unverified model prose in the assist response

The brief's hard requirement says assist "must not invent incident details that are not in retrieved records". Asking the model to stick to the records doesn't guarantee that, so internal assist output never contains free text written by the model:

- **Why each Incident is relevant (`reason`):** a short excerpt the model copies from that Incident's title, description or resolution summary. Code checks it appears in the stored record, ignoring case and whitespace, and requires at least 3 words. Citations that fail are dropped and logged.
- **`next_steps`:** built in code from each Cited Incident's stored `resolution_summary`, quoted word for word, plus fixed process steps that claim nothing about any Incident.
- **The model's role:** it chooses which Incidents are relevant, the evidence excerpts, and the customer-safe enums.

## Considered Options

- **Free-text reasons and next steps, constrained by the prompt:** rejected. Nothing can be verified, and a single invented detail breaks the requirement.
- **Next steps the model writes from evidence it cites per step:** rejected. Each step's wording would still be free text the model could embellish.

## Consequences

- Answers are more literal and less synthesised, and they repeat the records' wording.
- Verbatim matching is strict, so a model that paraphrases loses that citation. That costs recall, not safety. In live runs, the model quoted exactly.
- The Customer Draft (ADR 0003) is the one remaining place where model prose reaches a reader. For now it's treated as a draft that an operator reviews against the Cited Incidents. How strict the draft and next steps must be is an open product decision (see `TRADEOFFS.md`).
