# Live demo script (5 minutes)

Matches requirement 5.4 exactly: 5+ queries across different agent
routes, one failure case shown and explained, Docker containers visibly
running throughout. Timings are a guide, not a script to read verbatim —
practice it once or twice so the timing feels natural, not rehearsed.

**Before you start:** `docker compose up -d` (or `docker compose ps` to
confirm it's already up), `http://localhost:8080` open in a browser tab,
a terminal with `docker compose ps` ready in a second window/tab you can
flip to instantly.

**Record a full backup run-through beforehand** (any screen recorder —
even a phone pointed at the screen works). If anything breaks live —
container crash, network blip, a model having a bad day — say "let me
show you the recorded run" and play it, don't debug live in front of the
room. A calm switch to backup reads as prepared; frantic live debugging
reads as unprepared, even though the system clearly works (you have 200+
lines of test checklist proving it does).

---

## 0:00 – 0:30 — Open on the containers, one sentence on what this is

Terminal window:
```bash
docker compose ps
```
Say while it's on screen: *"Five containers — two independent agent
systems on different frameworks talking over a real network, an MCP
server, a vector database, and a frontend. This answers questions about
the VDA 5050 AGV/AMR communication standard, grounded in the actual spec
text, and validates messages against it."*

Switch to the browser, `http://localhost:8080`, start a new conversation.

---

## 0:30 – 1:15 — Query 1: ProtocolSpecialist route

**Ask:** `What is the MQTT topic structure in VDA 5050?`

**Expect:** prose answer covering `interfaceName/majorVersion/manufacturer/serialNumber/topic`.

**Say:** *"That routed to the Protocol Specialist — searches the spec's
prose sections."*

---

## 1:15 – 2:00 — Query 2: SchemaSpecialist route

**Ask:** `What are the required fields in an order message?`

**Expect:** the order.schema's required fields (`headerId`, `orderId`, `nodes`, etc.)

**Say:** *"Different specialist this time — Schema Specialist, searches
the JSON schemas instead of the prose spec. Same system, routed based on
what the question actually needs."*

---

## 2:00 – 2:45 — Query 3: DiagnosticsSpecialist route (Agent System B)

**Ask:** `What does the error type NODE_UNREACHABLE mean?`

**Expect:** CRITICAL severity, "cannot continue current order, can accept new orders."

**Say, while it's answering:** *"This one's different from the other
two — this isn't Agent System A searching anything. It's making a real
HTTP call to Agent System B, a completely separate service built on
Google's ADK instead of LangGraph, running in its own container. That's
the assignment's two-independent-agent-systems requirement — not a
Python import, an actual network call."* (Optional, if you have a second
terminal handy: flip to `docker compose logs agent-system-b -f` for two
seconds to show real request activity landing there.)

---

## 2:45 – 3:15 — Query 4: multilingual

**Ask:** `Quels sont les champs requis pour un message order ?`

**Expect:** full French prose, but `headerId`/`orderId`/etc. stay untranslated.

**Say:** *"Same schema question, in French this time — full answer in
French, but the actual field names stay as-is, because those are
identifiers, not vocabulary."*

---

## 3:15 – 4:15 — Query 5, AND the failure case: ambiguous routing

**Ask:** `Explain the map distribution process and list the required JSON fields`

**Expect:** this is a genuinely two-part question (a process question
*and* a schema question). Per the evaluation results, this reliably
routes to **ProtocolSpecialist** — not SchemaSpecialist, which is what
you might assume from "list the required JSON fields." It still answers
correctly (the map-distribution section of the spec text itself mentions
the required fields in prose), but it's the "wrong" specialist by the
test set's original ground truth.

**Say (this is your failure case — say it plainly, don't apologize for
it):** *"This is actually the failure case I want to walk through. This
question is genuinely ambiguous — half process question, half schema
question — and the Supervisor picked the process half. In our
evaluation, this shows up as a routing 'miss': 80% routing accuracy
overall, and this is one of the three misses in a 15-case test set. But
look at the answer — it's still complete and correct, because the spec's
own map-distribution section mentions the required fields in prose. So
this is a labeling ambiguity in our test set more than a system bug —
worth distinguishing those two things, and our evaluation report does
exactly that instead of just reporting one accuracy number."*

If asked "how do you know it's not a bug": *"Because we track fact
coverage separately from routing correctness — this case is 2 out of 2
on required facts, 100%, even though the route itself doesn't match what
we originally expected. That's in `docs/EVALUATION.md` section 4."*

---

## 4:15 – 4:45 — Back to the terminal — containers still running

```bash
docker compose ps
```
**Say:** *"Same five containers, still running, the whole time — nothing
was restarted or specially prepared for this demo."*

---

## 4:45 – 5:00 — Close

One sentence, pick whichever's true for your actual state at demo time:
*"Full evaluation — retrieval metrics across 7 configurations, generation
quality, agent routing accuracy, two required configuration comparisons,
and documented failure analysis — is in `docs/EVALUATION.md`, including
two real bugs we found and fixed during evaluation, with before/after
numbers proving the fixes worked."*

---

## If you have an extra minute and want a 6th query in reserve

**Ask:** `What's your favorite pizza topping?`

**Expect:** politely declined as out of scope — **not** a hard guardrail
block (that's reserved for genuinely malicious input), just a normal
"that's outside what I can help with" reply. Good if someone asks "what
happens with a totally unrelated question" — shows the guardrail design
without needing to explain it abstractly.

---

## Quick reference: which query proves which requirement

| Query | Proves |
|---|---|
| 1 (MQTT topics) | RAG retrieval over prose spec content |
| 2 (order fields) | RAG retrieval over JSON schema content, different specialist |
| 3 (NODE_UNREACHABLE) | Two independent agent systems, real network call |
| 4 (French) | Multilingual support, grounded generation |
| 5 (map distribution) | The failure case — routing evaluation, fact-coverage vs. routing-accuracy distinction |
| `docker compose ps` (throughout) | Dockerized, all 5 containers live |
