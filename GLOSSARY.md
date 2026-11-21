# Incident Triage

Support engineers search past Incidents and get AI-assisted, grounded suggestions for handling a new problem.

## Language

**Incident**:
A recorded operational problem in one Service and Environment, together with how it was resolved.
_Avoid_: Ticket, issue, case

**Service**:
One of a fixed set of product areas an Incident occurs in: payroll, timekeeping, auth, benefits, scheduling, reporting, notifications, integrations.
_Avoid_: Component, system, app

**Environment**:
The deployment stage an Incident occurred in: dev, qa, stage, or prod.
_Avoid_: Env, tier, stage (as a generic word)

**Severity**:
The impact level of an Incident, from SEV1 (most severe) to SEV4 (least severe).
_Avoid_: Priority, P1–P4, criticality

## Assist

**Candidate**:
An Incident retrieved by search for a question, and therefore eligible to be cited.
_Avoid_: Result, hit (in assist context)

**Cited Incident**:
A Candidate the assistant selected as relevant to the question, with a stated reason.
_Avoid_: Source, reference

**Customer Draft**:
A short reply meant for the affected customer, built only from customer-safe values and never from Incident free text.
_Avoid_: Response, customer message

**Internal Identifier**:
Any value that must never reach a customer, such as an Incident ID, hostname, IP address, email address, or request ID.
_Avoid_: PII (it is broader than personal data), secret
