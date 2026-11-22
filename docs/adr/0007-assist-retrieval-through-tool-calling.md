# Assist retrieves Candidates through LLM tool calling

The brief says assist "retrieves candidates using the same search code path as `/search` and logs it as a tool step (`tool:search`)". At first, code ran one fixed search with the engineer's question and handed the hits to the model. Now the model drives retrieval through a strict function tool, `search_incidents`. The `tool:search` log step then records what the model chose to search for.

- **Same code path:** the tool's handler builds a `SearchQuery` and calls `IncidentService.search()`, exactly like `GET /search`. It returns the total hit count and the full records of the top 10 Candidates, deduplicated by title.
- **Bounded:** one tool call per turn (`parallel_tool_calls=False`). The first turn must call the tool (`tool_choice="required"`), later turns may (`auto`), and the turn after 3 calls must answer (`none`). A tool call after the cap, or a call to an unknown tool, is an upstream error.
- **The engineer's filters win:** a filter set in the request overrides the model's value for it, and the override is logged. The model can narrow a search, for example by adding `service=auth`, but never widen it.
- **Errors go back to the model:** invalid arguments (a bad tag, a blank query with no filters, a query over 500 characters) return a tool error the model can correct. They never become a 4xx for the engineer.
- **Grounding unchanged (ADR 0005):** the guardrail checks citations against every record that any search returned.
- **`store=False` still holds:** OpenAI keeps no state, so each turn resends the full transcript. That includes the reasoning items with their `encrypted_content`, and the function calls with their outputs.

## Considered Options

- **Keep fixed retrieval** (code searches with the question, the model only ranks): rejected. It's cheaper and faster, but the model can't rephrase a question that matches badly, and "tool step" would only be a label.
- **The SDK's `max_tool_calls` parameter:** rejected in favour of an explicit loop. Controlling `tool_choice` per turn guarantees at least one search and a final answer, and keeps each step easy to log.

## Consequences

- Each search costs one extra LLM round-trip. Assist makes 2–4 LLM calls, and live runs took 3.7–9.6s, against 2.3–4.7s for one call.
- The shortcut "zero search hits means no LLM call" is gone, because the model runs before any search.
- Retrieval quality now depends partly on the model's queries. In live runs it pulled out the key error terms, added fitting filters on its own, and retried with other words when a search found nothing useful.
- Tool results are untrusted data. The prompt says to ignore any instructions inside them, and the guardrail still verifies every citation.
