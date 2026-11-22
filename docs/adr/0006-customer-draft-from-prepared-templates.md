# Customer Draft chosen from prepared templates

Supersedes [ADR 0003](0003-customer-draft-context-allowlist.md). The allowlisted draft call stopped Internal Identifiers from leaking, but the model still wrote the customer text, so it could claim incident details the records don't support. An external review flagged this against the brief's hard requirement ("must not invent incident details").

A Customer Draft never needs to describe an Incident. It does one of a few jobs, so the analysis call now picks one Customer Reply, and code returns that reply's fixed text:

| `customer_reply` | When | Draft says |
| --- | --- | --- |
| `request_details` | The question lacks what's needed to act. Always used on the insufficient paths. | Asks for the affected area, start time, error and scale. |
| `investigating` | Default | We're investigating and will get back to you. |
| `fix_in_progress` | The question says the cause is known and a fix is underway | We've found the cause and are working on a fix. |
| `fix_delivered` | The question says a fix has shipped | A fix has been delivered; reply with details if it persists. |

## Considered Options

- **Keep the LLM draft, with tighter instructions to stay on these themes:** rejected. Instructions aren't enforcement, and the draft would still be unverified prose.
- **An LLM draft whose claims are checked against the records:** rejected. It needs claim extraction and matching that's hard to get right, for little benefit over four sentences.

## Consequences

- No model writes customer-facing text, so nothing in the draft can be invented or leak an identifier. The allowlist, the second LLM call and the identifier backstop are gone.
- Assist makes one LLM call, which makes it faster and cheaper. Drafts are deterministic, which makes tests simple.
- Drafts are formulaic. Four coarse templates are enough for this prototype, and a production system would add more granular ones (for example per product area or per workaround).
- The choice of `fix_in_progress` or `fix_delivered` is the model's classification of the engineer's question, and code can't verify it. The prompt allows these only when the question says so, and a past Incident's resolution doesn't count. The default is `investigating`, so a wrong pick claims at most that work is underway.
