# The VDA 5050 Fleet Operations System — complete project guide

*Written as study material for defending this project on Friday. Every file, every decision, every trade-off, every known limitation — explained from first principles, assuming no prior background in any of these tools.*

---

## How to use this document

This is long on purpose — you said you'd rather have everything than a short version that leaves gaps. Read it in order once, then use it as a reference: Part 9 (decisions table) and Part 12 (cheat sheet) are built specifically so you can scan them the morning of the defense.

---

# Part 1 — What is this project, and why does it exist?

## 1.1 What is VDA 5050?

VDA 5050 is a communication standard. When a company runs a fleet of mobile robots on a factory or warehouse floor — think small robots that carry parts between stations — those robots need to talk to a central "fleet control" system that tells them where to go and what to do, and the robots need to report back their status, position, and any errors.

The problem VDA 5050 solves: before this standard existed, every robot manufacturer used its own private message format. A warehouse that bought robots from two different manufacturers couldn't run them under one fleet control system, because each brand "spoke" differently. VDA 5050 is a shared vocabulary — literally a set of JSON message formats and rules — that any manufacturer's robot and any fleet control system can both implement, so they can interoperate regardless of who built them. It was created jointly by two German industry associations (VDA, representing the automotive industry, and VDMA, representing mechanical engineering).

Concretely, the standard defines:
- **MQTT topics** — MQTT is a lightweight publish/subscribe messaging protocol (think: named "channels" that robots and fleet controllers publish messages to and subscribe to). VDA 5050 defines exactly how topic names are structured (`interfaceName/majorVersion/manufacturer/serialNumber/topic`).
- **JSON message schemas** — exact, precise definitions of what fields must appear in each type of message (an "order" telling a robot where to go, a "state" message reporting the robot's status, etc.), what type each field must be, which fields are required vs. optional.
- **Behavioral rules** — what a robot must do when it receives certain instructions, how it should behave when an error occurs, how order cancellation works, and so on.

The actual specification document (`data/raw_docs/VDA5050_EN.md` in this repo) is **2,175 lines** of dense technical prose, plus **8 separate JSON schema files** (order, state, instantActions, connection, visualization, factsheet, zoneSet, responses) totaling tens of thousands of characters each, plus 22 diagrams.

## 1.2 What problem does *this project* solve?

If you're an engineer working with VDA 5050 — say, debugging why a robot rejected an order, or building software that needs to emit valid VDA 5050 messages — you currently have to manually search through that 2,175-line document and cross-reference JSON schema files by hand. That's slow, error-prone, and genuinely unpleasant for anything beyond a quick lookup.

This project is an AI assistant purpose-built for that job. It can:
1. **Answer questions about the protocol rules** in plain language, citing the actual spec content (not guessing).
2. **Answer questions about the JSON schemas** — required fields, data types, nested structures.
3. **Validate a real JSON payload** against the actual schema and report exactly what's wrong, field by field.
4. **Look up what a specific error code means** — its severity, what the robot is expected to do, straight from the standard's own error table.
5. **Accept custom, manufacturer-specific documents** a user uploads (e.g. a company's own protocol extension) and answer questions about those too, alongside the official spec.
6. **Work in multiple languages** — a French or Spanish-speaking engineer can ask questions in their own language and get answers in that language, with the technical vocabulary (field names, error codes) left untranslated because those are literal identifiers, not English words.

## 1.3 Why build it this way (multi-agent, RAG, containers)?

Two forces shaped the architecture:

1. **The assignment's requirements** — you were explicitly asked to build two independently-developed agent "systems" on different frameworks that talk to each other over a real network connection (not one calling the other as a Python function), a separate tool server following the Model Context Protocol (MCP), a proper RAG (retrieval-augmented generation) pipeline with a real vector database, and containerized deployment. These aren't arbitrary hoops — they mirror how real engineering organizations actually build this kind of system, where different teams own different services and integrate over network APIs rather than shared codebases.

2. **Real engineering needs** — even setting the assignment aside, an AI assistant that answers from a 200KB specification document genuinely needs retrieval (an LLM can't just "know" your specific uploaded documents or reliably recite a huge spec from memory without hallucinating), and a task like "validate this JSON against a schema" is fundamentally a job for deterministic code (a JSON Schema validator), not something you'd want an LLM improvising.

We'll walk through every one of these pieces in detail below.

---

# Part 2 — The big picture: five containers, one system

Here's the architecture diagram from earlier in our conversation, for reference as you read this section:

*(See the diagram rendered above — Chatbot UI → Agent System A → [MCP Server → Vector DB] and → Agent System B)*

## 2.1 What's a "container" and why five of them?

A **container** (Docker container) is a small, isolated, self-contained package that runs one piece of software with its own dependencies, completely separate from everything else running on the machine — like a shipping container that can be loaded onto any ship regardless of what's inside it. `docker-compose.yml` describes five containers and how they're networked together:

| Container | What it is | What it does |
|---|---|---|
| `vector-db` | Qdrant (official image) | The actual searchable database of document chunks |
| `mcp-server` | Custom Python service | Owns the search/upload/delete tools, talks to Qdrant |
| `agent-system-a` | Custom Python service (FastAPI + LangGraph) | The "brain" — routes questions, talks to the user, calls tools |
| `agent-system-b` | Custom Python service (FastAPI + Google ADK) | The diagnostics specialist — validates payloads, looks up error codes |
| `chatbot-ui` | Static HTML/CSS/JS + nginx | What the user actually sees in the browser |

Why not just one big program? Separation of concerns, mirroring the assignment's real-world lesson: each piece can be built, tested, restarted, and even replaced independently. If `agent-system-b` crashes, `agent-system-a` can still answer protocol/schema questions — it just can't reach the diagnostics specialist until B comes back. If it were all one program, one bug anywhere could take down everything.

`docker-compose.yml` sets each container's environment variables (API keys, URLs pointing at each other by container name — e.g. `MCP_SERVER_URL=http://mcp-server:8001/mcp`, using Docker's built-in DNS so containers can find each other by name), which folders are shared (`./data:/app/data` — the host machine's `data/` folder is mounted into multiple containers so they all see the same files), and startup order (`depends_on`).

## 2.2 What travels between them, and how

- **Browser ↔ Agent System A**: HTTP requests. Either a normal request/response (`POST /conversations/{id}/messages`) or the new SSE streaming variant (`POST /conversations/{id}/messages/stream`) — more on this in Part 4.
- **Agent System A ↔ MCP Server**: the **Model Context Protocol** (MCP) over HTTP — explained fully in Part 6.
- **Agent System A ↔ Agent System B**: plain HTTP + JSON (`POST /agent/ask`).
- **MCP Server ↔ Vector DB**: the Qdrant client library, talking to Qdrant's own HTTP API.

---

# Part 3 — Data & the RAG pipeline (`mcp-server`, `vector-db`)

This is the retrieval half of "retrieval-augmented generation." Before we get into the pipeline, let's build the core concept from scratch.

## 3.1 What is RAG, actually?

**The problem**: an LLM's "knowledge" comes from what it was trained on. It wasn't trained on your specific uploaded document, and even for the official VDA 5050 spec, you don't want it reciting from fuzzy memory — you want it reading the actual current text and answering from that, the same way you'd want a person to open the actual spec PDF rather than answer from memory.

**The RAG idea**: before the LLM answers, first *search* a database of your actual documents for the most relevant pieces, then hand those pieces to the LLM as part of its prompt ("here's what the spec actually says about X — now answer the question using this"). This is why our specialists' system prompts include an explicit instruction: *"Answer only using what your tool calls actually return — do not fill gaps from general knowledge."* That's the whole RAG contract in one sentence: search first, answer from what you found, don't guess.

## 3.2 What is a "vector database" and why do we need one?

A normal database (like SQL) finds things by *exact* matches — `WHERE error_type = 'NODE_UNREACHABLE'`. That's perfect for Agent System B's error lookup table (an exact key lookup — no AI needed there at all, see Part 5). But for "find the parts of a 2,175-line spec relevant to this question," exact keyword matching isn't good enough — a question phrased differently from the spec's own wording (e.g. "how often should a heartbeat be sent" vs. the spec's actual wording) would miss.

**Embeddings** solve this. An embedding model converts a piece of text into a list of numbers (a "vector") that captures its *meaning* — texts with similar meaning end up as vectors that are mathematically close to each other, even if they don't share any exact words. A **vector database** (we use **Qdrant**) is a database purpose-built to store millions of these vectors and quickly find "which stored vectors are closest to this new query's vector" — this is called **similarity search**.

Analogy: imagine a library where books aren't shelved alphabetically by title, but physically placed near other books about similar *topics*, regardless of title wording. You could walk up with a vague description of what you want and get directed to the right shelf, even without knowing the exact title.

**The embedding model we use**: `BAAI/bge-m3` (set in `services/mcp-server/core/config.py`). This one detail matters a lot for your multilingual requirement — `bge-m3` is specifically a **multilingual** embedding model, meaning a French question and the English source text can still end up close together in vector space, because the model understands meaning across languages, not just within English. This is a real, deliberate choice supporting the multilingual feature at the retrieval layer, not just the generation layer.

## 3.3 The ingestion pipeline: how documents become searchable chunks

Two files matter here: `services/mcp-server/core/ingestion.py` (the logic) and `services/mcp-server/core/run_ingestion.py` (the offline script that runs it once against the shipped spec files).

**Step 1 — Loading** (`load_documents`): scans `data/raw_docs/` recursively and loads every file with extension `.md` or `.schema` — **and only those two extensions**. This is important and we'll come back to it in 3.6.

**Step 2 — Chunking**: an LLM can't usefully digest a 2,175-line document or a 46,000-character JSON schema in one search result — you need to break it into smaller pieces ("chunks") that are each focused enough to be a good, precise search result. But *how* you chunk matters enormously for retrieval quality, which is why this project uses **two different chunking strategies**, chosen by file type:

- **JSON schemas** (`_chunk_json_schema`): schemas are structured data, not prose, so they get structure-aware chunking:
  - One chunk = the schema's overview (title, description, list of required top-level fields).
  - One chunk per top-level property (or one combined chunk if the whole property list is small enough).
  - One chunk per `definition` (a schema sub-structure, like the `node` or `edge` definitions inside `order.schema`) — and if a single definition is very large, it's further split into one chunk per property *within* that definition.

  Why this matters: if a user asks "is `sequenceId` required in the connection schema," a naive text-splitter chunk boundary might cut that field's definition in half, or bury it in a huge blob with fifteen unrelated fields. Structure-aware chunking means each chunk is a complete, self-contained, focused unit — the exact granularity a question like that needs.

- **Markdown** (`_chunk_markdown`): uses `MarkdownHeaderTextSplitter` to split along the document's own `#`/`##`/`###` headers, so each chunk stays within one logical section rather than crossing section boundaries arbitrarily. Every chunk gets its header path prepended (e.g. `[Section: 6.6 Error Handling > 6.6.5 Error Codes]`) so the LLM sees where in the document this piece came from, even out of context. Any section still too big (over 1,500 characters) gets further split by a generic recursive character splitter as a fallback.

- **Fallback** (`_chunk_text_fallback`): a plain, generic splitter used only if the above two fail (e.g. malformed JSON) — chunk size 1000 characters, 100 character overlap between chunks (these numbers are configurable in `core/config.py`, and are exactly the kind of thing worth testing different values of in your evaluation — see Part 11).

**Step 3 — Embedding & storing**: every chunk's text goes through the `bge-m3` embedding model, and both the vector and the chunk's metadata (source filename, chunk type, schema name, etc.) get stored in Qdrant.

`run_ingestion.py` is the script you run manually once (`docker compose exec mcp-server python -m core.run_ingestion`) to (re)build the entire base corpus from the shipped spec files. It also saves all the chunks to a plain pickle file (`data/qdrant_db/../chunks.pkl`) for a second purpose explained next.

## 3.4 The retriever: hybrid search + reranking

This is the most sophisticated part of the pipeline, and it's worth understanding precisely because there's a real trade-off buried in here (see 3.5).

`services/mcp-server/core/retriever.py`'s `get_retriever()` function, when called **without** a `conversation_id`, builds a three-stage pipeline:

1. **Dense retrieval** (Qdrant, meaning-based search) — finds the top ~10 chunks by embedding similarity.
2. **Sparse retrieval** (`BM25Retriever`, loaded from that `chunks.pkl` file) — a classic *keyword*-based search algorithm (no AI, just term-frequency statistics). BM25 is genuinely good at exact-term matches that embeddings can sometimes fuzz over — if someone asks about `headerId` specifically, BM25 is very good at making sure chunks containing that literal token rank highly, which a pure embedding search might not guarantee.
3. **Ensemble** (`EnsembleRetriever`, 50/50 weight) — combines both result lists into one, so you get the benefits of both semantic and exact-keyword matching.
4. **Reranking** (`CrossEncoderReranker`, using `BAAI/bge-reranker-base`) — a *cross-encoder* jointly looks at the query and each candidate chunk together (more expensive than embeddings, but more accurate) and re-scores the top candidates, keeping only the best `top_n` (default 3, `RETRIEVER_K` in config). Think of dense+sparse retrieval as a fast first pass that narrows a huge library down to 10 plausible candidates, and the reranker as a slower, more careful judge that picks the best 3 from those 10.

This is genuinely a strong, "textbook-correct" retrieval pipeline — hybrid retrieval + reranking is a well-established best practice, and it's real, working code in this repo.

## 3.5 The trade-off you should know about (and can turn into an evaluation experiment)

Here's the catch, and it's worth understanding precisely because it's the kind of thing a grader might probe:

`get_retriever()` only builds the full hybrid+rerank pipeline **when no `conversation_id` is passed**. When a `conversation_id` **is** passed (which is required to make per-conversation and global uploads discoverable — see 3.6), the function instead returns a much simpler retriever: **pure dense (Qdrant) search only** — no BM25, no reranker.

When I fixed the upload-scoping feature, I updated the specialists' instructions to **always** pass `conversation_id` on every search (not just as a fallback retry), so that uploaded documents are always discoverable. The side effect — which I didn't fully register until reviewing the code for this document — is that this means **every single search now goes through the plain dense-only path**, and the carefully-built hybrid+reranker pipeline is, in the currently deployed behavior, **never actually used in production**, even for questions with zero uploaded documents involved.

This is a real, honest trade-off, not a bug I'm hiding: making uploads work correctly cost you the higher-quality retrieval pipeline for every query, not just upload-related ones. I'm flagging it explicitly here because:
1. **It's the kind of thing you should be able to explain if asked**, rather than be caught off guard by.
2. **It's a genuinely great candidate for one of your two required evaluation config comparisons** (see Part 11) — you already have real, working code for both configurations (hybrid+rerank vs. dense-only); you'd just need to compare their retrieval metrics (Precision@3, Recall@3, MRR, NDCG) against your test set. That's a real, meaningful, easy-to-run experiment already sitting in your codebase.
3. **A cleaner long-term fix** (if you have time) would be to make `get_retriever()` run the full hybrid+rerank pipeline even when `conversation_id` is set — by combining the *base corpus* through the hybrid path and the *conversation-specific* chunks through a separate dense-only lookup, then merging results — rather than the current all-or-nothing branch. That's a real, scoped, explainable improvement if you want to make it before Friday; I did not make this change today since it touches retrieval logic close to your deadline and deserves its own testing pass, but it's a legitimate "if I had more time" answer for a defense question.

**Update, after actually running the evaluation (Part 13):** building configuration comparison A surfaced a *second*, smaller, distinct bug in the same function: the `conversation_id` branch was hardcoded to `search_kwargs={"k": fetch_k}` (fetch_k=10) instead of the caller's requested `k` — with no reranking afterward to cut that back down, so every real production call wasn't just missing hybrid+rerank, it was also silently returning 10 chunks instead of 3, unranked beyond raw cosine similarity. Unlike the bigger merge fix above, this one **was** made and **confirmed by re-running the evaluation**: before the fix, the deployed path's Recall@3 was 0.385; after, it's 0.833 — identical to the plain dense-only config with no `conversation_id`, proving the branch now behaves exactly as a dense search should. The remaining, smaller gap between deployed (0.574 precision) and intended (0.648 precision) is the real, single, intentional trade-off — no hybrid, no reranking — not that trade-off plus an accidental second regression. See `docs/EVALUATION.md` §5 and §7 for the full before/after.

## 3.6 Does the RAG system have every file? (Your direct question — answered precisely)

**Short answer: no.** Here's exactly what's in and what's out, checked directly against the actual files in `data/raw_docs/`:

**In the searchable corpus:**
- `VDA5050_EN.md` — the full 2,175-line spec text. ✅ Fully searchable.
- All 8 `.schema` files (order, state, instantActions, connection, visualization, factsheet, zoneSet, responses). ✅ Fully searchable, with the structure-aware chunking described above.
- `test_upload_document.md` and 8 `*_UML.md` files in `data/raw_docs/assets/`. ✅ Searchable — because `load_documents()` only filters by extension (`.md`/`.schema`), and these UML files happen to have a `.md` extension.

**What those 8 `*_UML.md` files actually are, and why they matter**: the spec's process diagrams (like "what happens when a `cancelOrder` action arrives") are shipped as both a `.png` image *and* a companion `.md` file containing the **PlantUML source code** that generated that image — essentially a structured, text-based description of the same flowchart (steps, branches, conditions), which an LLM can read and reason about even though it's not the picture itself. This is a clever existing setup: it means 8 of the more complex process diagrams *are* effectively searchable in text form, even though the pipeline has no image-processing capability at all.

**What's completely invisible to the RAG system:** I checked every image reference against the available UML sidecars, and **14 of the 22 diagram images in `data/raw_docs/assets/` have zero text representation anywhere in the corpus:**

```
action_state_transition.png          driving_route_horizon.png
contour_entry.png                     edges_with_corridors.png
coordinate_system_vehicle_orientation.png   ellipse.png
csagv.png                             graph_representation_transmission.png
kinematic_center_entry.png            logo.png
order_information_state_topic.png     states_during_order_handling.png
update_order_extension.png            update_order_stitching_node.png
```

If someone asks a question whose answer genuinely depends on one of these diagrams (e.g. "what does the coordinate system for vehicle orientation look like," or "explain the graph representation diagram"), the RAG system has nothing to retrieve — it would either say it doesn't know, or (worse, if the model isn't careful) hallucinate a plausible-sounding but made-up description. This is a real, concrete limitation worth naming explicitly rather than discovering live in a demo.

One more thing worth knowing: `run_ingestion.py`'s console banner text says *"Structure-Aware Chunking + Vision Descriptions."* **There is no vision/image-processing code anywhere in this codebase.** This banner text is stale/aspirational — probably describing an earlier plan that was never implemented — and should either be removed or the feature actually built. I'm flagging this so you're not surprised if a grader reads that banner and asks about "vision descriptions" that don't actually exist.

## 3.7 Uploads: global vs. per-conversation, explained mechanically

Every chunk stored in Qdrant carries metadata, including (optionally) a `conversation_id` field. Think of it as a sticky note on each chunk:

- **Per-conversation upload**: the chunk's sticky note says `conversation_id: <that conversation's ID>`. `get_retriever()`'s filter, when searching *that* conversation, matches chunks with that exact ID *or* chunks with no `conversation_id` sticky note at all (the base corpus). Chunks with a *different* conversation's ID never match — that's the isolation.
- **Global (Knowledge Base) upload**: the chunk simply gets **no** `conversation_id` sticky note at all — same as the base corpus. This means it's automatically picked up by *every* conversation's search, with zero extra code needed to make it "global" — it just falls into the same bucket as the official spec files.

This mechanism lives in `services/mcp-server/server.py`'s `ingest_document` tool (conversation_id is an optional parameter; omit it → global) and `core/retriever.py`'s Qdrant filter (`IsEmptyCondition` on the `conversation_id` field — literally "does this chunk have no conversation_id at all").

**The bug we found and fixed this week**: deleting a document was silently broken for months, for both upload types, because of a subtle ID mismatch — the ID stored in the small SQLite table that the UI reads (to show the document list) was being generated *fresh*, separately from the ID already stamped onto the actual chunks in Qdrant. So "delete" removed the row from the UI's list (looked successful) but told Qdrant to delete chunks matching an ID that matched *nothing* — the real content was never touched. Fixed by making both places use the exact same ID. This is a good example of a bug that "looks fixed" from the UI's perspective but isn't — worth remembering as a general lesson: always verify a delete actually happened in the underlying store, not just that the UI stopped showing it.

**The remaining, expected limitation**: even with that bug fixed, a conversation that *already discussed* an uploaded document's content before you delete it can keep answering about it — not because deletion failed, but because LangGraph's own conversation memory (Part 4.6) already has that earlier answer saved in its own message history, independent of whatever is or isn't currently in Qdrant. Analogy: you can shred your notes, but if you already texted someone the fact, shredding the notes doesn't un-send the text. This is a real, named, accepted limitation (documented in `docs/ARCHITECTURE.md`), not a bug — the delete mechanism itself works correctly; it just can't reach into a conversation's already-generated replies.

## 3.8 The five MCP tools, in plain terms

| Tool | What it does |
|---|---|
| `search_protocol_rules` | Searches the markdown spec (+ uploads) for rule/behavior questions |
| `search_json_schemas` | Searches the JSON schemas (+ uploads) for structure/field questions |
| `ingest_document` | Chunks and stores a new document (conversation-scoped or global) |
| `delete_document` | Removes every chunk belonging to one document |
| `delete_conversation_documents` | Removes every chunk from every document in one conversation (used when an entire conversation is deleted) |

---

# Part 4 — Agent System A: the LangGraph brain

## 4.1 What is LangGraph, and what's a "graph" here?

LangGraph lets you describe an AI agent's behavior as a **graph** of steps ("nodes"), with rules ("edges") for which step happens next based on what the current step produced — essentially a flowchart that a program actually executes, where some of the decision points are made by calling an LLM rather than by a hardcoded `if` statement.

Compare this to the simplest possible design — one LLM call with a giant prompt and a pile of tools, deciding everything itself in one shot. LangGraph's explicit graph structure instead lets you build in guardrails, routing logic, retries, and step limits as real, inspectable code around the LLM calls, rather than hoping one enormous prompt gets everything right. This is exactly why the graph has a dedicated `InputGuard`, a `Supervisor` that only *routes* (doesn't answer directly), separate specialist nodes, and a dedicated `OutputGuard` — each piece has one job.

## 4.2 The full node list, and the actual flow

From `services/agent-system-a/agent/graph.py`'s `build_graph()`:

```
START → InputGuard → Supervisor → (route to one of:)
                                    ├─ ProtocolSpecialist ⇄ protocol_tools
                                    ├─ SchemaSpecialist ⇄ schema_tools
                                    ├─ DiagnosticsSpecialist ⇄ diagnostics_tools
                                    ├─ SmallTalk (handled inline in Supervisor)
                                    └─ FINISH (handled inline in Supervisor)
                                   → back to Supervisor (if a specialist answered)
                                   → OutputGuard → END
```

Every specialist, after finishing a tool call, loops back to its own tool node and then back to itself (the ReAct loop — see 4.4) — and once it produces a final text answer (no more tool calls), the graph routes back to **Supervisor** one more time. The Supervisor recognizes this case immediately (`last message is AI with no tool_calls` → `next = FINISH`, no LLM call needed) and hands off to `OutputGuard` → `END`. So the Supervisor genuinely runs twice per typical turn: once to route, once to notice the specialist is done.

## 4.3 InputGuard and OutputGuard — what they actually check

Be precise about these, because "guardrail" can sound more sophisticated than what's implemented, and you should be able to describe exactly what exists:

- **InputGuard** (`input_guard`): checks only that the message isn't empty. That's it. It used to hard-block any message that didn't contain one of ~25 hardcoded keywords (meaning "hello" was blocked before the graph even started) — this was a real bug we fixed this week, moving all topic-relevance judgment into the Supervisor's SmallTalk/FINISH classification instead (4.5), which can actually understand a message rather than pattern-match it.
- **OutputGuard** (`output_guard`): checks the final reply text for two literal red flags — the substring `"[insert"` (a placeholder the model forgot to fill in) or `"example.com"` (a fake/hallucinated domain). If either appears, it replaces the reply with a `GUARDRAIL_BLOCK:` message instead of showing the placeholder text to the user.

Honest framing for a defense question: this is a **shallow, pattern-based safety net**, not a robust content-safety system — appropriate for this project's actual risk profile (an internal technical assistant, not a public-facing product handling adversarial users), but you should describe it accurately rather than imply it's more sophisticated than it is.

## 4.4 The ReAct loop, and why it can't stream token-by-token

Every specialist follows the same pattern (implemented once, in `create_agent_node`, and reused by all three specialists — see 4.9):

1. Look at the conversation and the question.
2. The LLM decides: answer now, or call a tool first?
3. If it calls a tool, the `ToolNode` runs it and feeds the result back.
4. Loop back to step 2 (up to `MAX_TOOL_STEPS = 3` times, then it's forced to answer with whatever it has).

This is called **ReAct** (Reason + Act). The critical detail: step 2's decision depends on a special field on the LLM's response called `tool_calls`, which is only knowable once the response is **completely finished** generating. This is exactly why we can't do true word-by-word streaming for these specialists (explained in detail back in our SSE conversation, and in Part 4.8) — you can't know whether the model is about to say "let me search for that" until it's done thinking, and a half-shown sentence that gets thrown away mid-stream would be worse UX than the step-level status updates we actually built.

## 4.5 The Supervisor: routing, and the SmallTalk/FINISH split

The Supervisor doesn't answer questions itself — its only job is deciding *who should*. Mechanically:

1. Checks a hard iteration cap (`MAX_ITERATIONS = 3`) — if exceeded, it stops and returns whatever partial answer exists rather than looping forever.
2. Checks a **deterministic regex override** (`_ERROR_TOKEN_PATTERN`): if the question contains an ALL_CAPS_WORD-style token like `NODE_UNREACHABLE` or `BANANA_ERROR`, it routes straight to `DiagnosticsSpecialist`, skipping the LLM router call entirely. **Why**: in real testing, the small router model was inconsistently misrouting exactly this pattern (`NODE_UNREACHABLE` and `LOCALIZATION_ERROR` both got wrongly sent to `FINISH` at one point). Since this specific pattern can be detected with plain code, 100% reliably, there's no reason to leave it to a chance LLM judgment call — a general engineering principle: **if you can detect something deterministically in code, don't delegate that decision to a probabilistic model.**
3. Otherwise, calls a small LLM (see 4.7) with structured output — it must return exactly one of `ProtocolSpecialist`, `SchemaSpecialist`, `DiagnosticsSpecialist`, `SmallTalk`, or `FINISH` (enforced by a Pydantic model, `Route`, not free text).
4. **SmallTalk** vs **FINISH** — two different flavors of "not a technical question," added this week to fix a real bug (greetings were getting hard-blocked): SmallTalk is genuine social pleasantries ("hi," "thanks," "bye") and gets a short, warm, on-topic-redirecting reply from a separate lightweight LLM call (`get_smalltalk_responder`). FINISH is a genuinely unrelated question (football scores, recipes) and gets a fixed "outside scope" decline.

## 4.6 Two different kinds of memory (don't confuse them)

This system has **two completely separate persistence mechanisms**, doing two different jobs — worth being crystal clear on this distinction:

1. **LangGraph's checkpointer** (`AsyncSqliteSaver`, writing to `data/checkpoints.db`): this is what makes a conversation "remember" earlier turns. Every request passes a `thread_id` (the conversation's ID); LangGraph automatically loads that thread's *entire prior state* (every message, every intermediate value) before running, and saves the new state afterward. This is a framework feature — we didn't write conversation memory ourselves, we just plugged the framework's existing solution in.
2. **`conversations.db`** (plain SQLite, hand-written in `api/db.py`): a much smaller, simpler database holding only lightweight metadata — conversation titles and timestamps for the sidebar list, and records of which documents were uploaded where (filename, upload time, chunk count). This does **not** hold the actual chat messages — that's the checkpointer's job.

These are deliberately two separate SQLite files rather than one combined database: the checkpointer's schema is owned entirely by LangGraph (upgrading LangGraph could change that schema's internal shape), while `conversations.db`'s schema is ours to control freely. Mixing them would create an unnecessary coupling.

## 4.7 Model choices, and the Groq deprecation story

Every LLM call in this graph goes through **Groq** (a fast inference provider) as the primary, with **Google Gemini** models as automatic fallbacks if Groq fails (rate limit, outage, or a decommissioned model), via LangChain's `.with_fallbacks([...])` — if the primary call raises an exception, it automatically retries with the next model in the list.

| Role | Model | Why |
|---|---|---|
| Supervisor router | `openai/gpt-oss-20b` (Groq) → `gemini-3.1-flash-lite` | Needs structured output (a strict enum choice), runs on every single turn (sometimes twice), so cheap/fast matters most; doesn't need to be the smartest model, just a reliable classifier |
| Specialists (Protocol/Schema/Diagnostics-relay) | `openai/gpt-oss-120b` (Groq) → `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite` | Actually writes the final answer shown to the user — needs more capability than the router |
| SmallTalk responder | `openai/gpt-oss-120b` (Groq) → `gemini-3.1-flash-lite`, temperature 0.4 | Higher temperature than the others (0.1) on purpose — a slightly warmer, less robotic tone fits a "hello, how can I help" reply better than a technical answer, where you want low temperature for consistency |

**Why these specific models, right now**: Groq sent two separate deprecation notices — `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` (this system's *original* supervisor and specialist models respectively) were both decommissioned on August 16, 2026. Both had to be migrated. `openai/gpt-oss-20b`/`120b` were chosen as Groq's own recommended free-tier replacements, confirmed against Groq's documentation. A code comment in `graph.py` also notes a **removed** third fallback (a local Ollama model) — it used to sit in the fallback chain, but there's no Ollama container in `docker-compose.yml`, meaning inside any real container it pointed at nothing; a guaranteed-broken fallback turns "everything failed gracefully" into "everything failed with a hard crash" once the chain is exhausted, so it was deleted rather than left as silent dead weight.

**Is the current setup enough for heavy evaluation/testing load?** Be honest about this if asked: free-tier Groq quotas (30 requests/min, ~1,000 requests/day for the current models) are fine for interactive demo use, but your own Groq dashboard screenshots this week showed token usage brushing the rate-limit line during ordinary manual testing. A real evaluation run (dozens of LLM-as-judge calls back to back) will likely lean on the Gemini fallback often, or hit rate limits outright. Plan for that explicitly in your evaluation write-up rather than being surprised mid-run.

## 4.8 SSE streaming — the full explanation, tied to the code

*(This restates and formalizes what we walked through conversationally earlier — included here so it's all in one document.)*

**The concept**: normal HTTP is "ask, wait in silence, get one complete answer." **Server-Sent Events (SSE)** keeps the connection open and lets the server send several small updates over time as they become ready, one direction only (server → browser) — which is all a chatbot reply needs.

**Why we needed it**: your assignment explicitly requires at least one streaming endpoint, and it's genuinely better UX — instead of staring at blank "thinking" dots for several seconds, the user sees live status ("Routing your question…" → "Searching VDA 5050 protocol rules…" → answer).

**Why step-level, not token-level (word-by-word)**: as explained in 4.4, every specialist call is a plain, synchronous `.invoke()` specifically *because* it needs the model's complete response (including the `tool_calls` field) before it can decide whether to loop through a tool or stop. You cannot safely show a user partial words that might get discarded mid-thought. Rewriting this into a token-streaming-compatible design would mean restructuring the entire ReAct loop across all three specialists — a large, risky change this close to your deadline, for a requirement that step-level streaming already satisfies. This is a legitimate "we chose not to, and here's precisely why" answer, not something to be defensive about.

**How it's actually implemented** (`services/agent-system-a/api/main.py`):
- `_run_graph()` — the single, shared async generator that drives the graph via `app.state.graph.astream(input_state, config=config, stream_mode="updates")`. `stream_mode="updates"` tells LangGraph: "give me an event the instant each node finishes, containing exactly what changed." This needed **zero changes** to any node's internal logic — it's purely a different way of *running* the same graph.
- `POST /conversations/{id}/messages` (non-streaming): consumes `_run_graph()` fully, keeps only the final answer, returns it as one JSON object.
- `POST /conversations/{id}/messages/stream` (SSE): consumes the same `_run_graph()`, and instead formats each event as a `data: {...}\n\n` line (the entire SSE wire format — literally just plain text lines with that prefix) as it arrives, then a final `data: {"done": true, "reply": "..."}` line.

**Why both endpoints share one function now (the duplication you noticed)**: originally, the non-streaming endpoint called `graph.ainvoke()` directly and the streaming endpoint separately called `graph.astream()` — two different code paths driving the same graph, which is exactly the kind of duplication that quietly drifts (a fix applied to one and forgotten in the other). This was refactored this week so `ainvoke` is no longer called anywhere in the file — there is now exactly **one** invocation path (`_run_graph`, using `astream`), and the two endpoints differ only in what they *do* with its output, not in how they run the graph. I tested this directly (built a mock graph shaped like the real one, ran both endpoints against it, confirmed identical final answers) before considering it done.

**Frontend side** (`services/chatbot-ui/index.html`): uses `fetch()` + the browser's `ReadableStream` API, **not** the built-in `EventSource` object — `EventSource` only supports GET requests, and sending the user's typed message requires a POST body, so it doesn't fit. `sendMessage()` reads the response in chunks as they arrive, splits on the SSE frame boundary (`\n\n`), parses each `data: ...` line as JSON, and updates the "thinking" indicator's text live as status events arrive.

## 4.9 The shared specialist factory: `create_agent_node`

Rather than writing three nearly-identical functions for Protocol/Schema/Diagnostics, there's one factory function, `create_agent_node(tools, system_prompt)`, called three times with different tools and prompts. Inside, on every call it:

1. **Detects the reply language** (`_detect_reply_language`, using the `langdetect` library) from the user's actual last message — computed in *code*, not left to the model to infer. This replaced an earlier, less reliable approach (a plain system-prompt instruction telling the model to "reply in the user's language") that broke down specifically in conversations that had already mixed several languages across turns — confirmed by real testing where English questions came back in Chinese, Japanese, or German. Detecting the language deterministically and stating it explicitly, per turn, removes that judgment call from the model.
2. **Injects a language reminder** (`_with_language_reminder`) onto a *copy* of the last human message (never written back to the persisted conversation state, so this reminder text never pollutes the saved history or gets re-detected later) — placed at the very end of the actual prompt, where a model's attention is strongest right before it generates.
3. Builds a `context_note` telling the specialist the current `conversation_id` and instructing it to always pass that to its search tools (see Part 3.7 for what that unlocks — and Part 3.5 for its cost).
4. Enforces `MAX_TOOL_STEPS = 3` — after that many tool calls, it forces a final answer using only whatever was already retrieved, rather than looping forever.
5. Normalizes the LLM response's `.content` to a plain string via `_as_text()` — a small but important defensive helper, because some providers (notably certain Gemini responses) can return a *list* of content parts instead of a plain string, which would otherwise silently break anything downstream expecting a string, or render as literal `"[object Object]"` in the frontend.

Each specialist's actual system prompt is built from shared instruction blocks (`GROUNDING_INSTRUCTION`, `FRESHNESS_INSTRUCTION`, `LANGUAGE_INSTRUCTION`) concatenated together, rather than three separately hand-written prompts — keeping the shared rules (grounding, re-search freshness, language matching) consistent across all three specialists by construction, instead of relying on three copies staying in sync by discipline.

## 4.10 The REST API surface, end to end

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `POST /conversations` | Create a new conversation |
| `GET /conversations` | List conversations (sidebar) |
| `GET /conversations/{id}` | Full message history for one conversation |
| `POST /conversations/{id}/messages` | Ask a question, wait, get the full reply |
| `POST /conversations/{id}/messages/stream` | Same, but SSE-streamed step-by-step |
| `DELETE /conversations/{id}` | Delete a conversation and its per-conversation uploads |
| `POST /conversations/{id}/documents` | Upload a document scoped to one conversation |
| `GET /conversations/{id}/documents` | List that conversation's uploads |
| `DELETE /conversations/{id}/documents/{doc_id}` | Delete one per-conversation upload |
| `POST /documents` | Upload a document to the global Knowledge Base |
| `GET /documents` | List global Knowledge Base documents |
| `DELETE /documents/{doc_id}` | Delete one global document |

---

# Part 5 — Agent System B: the Google ADK diagnostics agent

## 5.1 The big question: why two agent systems instead of one?

You asked this directly, so let's answer it honestly and completely, including the parts that don't flatter the design.

**The assignment reason (the primary, real reason)**: you were explicitly required to build two independently-developed agent systems on genuinely different frameworks, communicating over a real network call — not one calling the other as a Python function within the same process. The pedagogical point is learning to integrate with a service you don't personally own or control the internals of, the way real engineering teams integrate with services built by other teams (or other companies) using entirely different tech stacks. That's exactly what LangGraph-in-A talking to Google-ADK-in-B, over plain HTTP, demonstrates.

**Legitimate architectural merits, beyond the assignment**: Agent System B's actual job — validate a JSON payload against a schema, look up a fixed error code table — is fundamentally different in *character* from Agent System A's job (open-ended retrieval and conversation). B's logic is deterministic and narrow; A's is exploratory and broad. Splitting them means:
- B can be deployed, restarted, or scaled independently of A.
- A degrades gracefully if B is unreachable (returns "could not reach the diagnostics agent" rather than crashing the whole chat) — this only works *because* it's a real network call with a timeout, not a function call.
- In a real organization, a different team (with different tooling preferences) could own and ship B's validation logic on its own release schedule without needing write access to A's codebase at all.

**The honest cost**: an extra container, network latency between A and B, another timeout to manage (`ask_diagnostics_agent`'s `httpx` call has a 25-second timeout), another point of failure, a separately-billed model call (Gemini via ADK). **If you're asked "was splitting into two systems the best design, purely on engineering merits" — the honest answer is**: for a project this size, one well-organized system probably would have been simpler, and there's nothing architecturally *wrong* with a single system (in fact, this is literally how it worked before this week — Agent System A called B's raw endpoints directly, which functioned correctly). The split exists primarily because the assignment explicitly requires it and because it's a genuinely valuable exercise in service integration — not because two systems are inherently superior to one for a project of this scope. Being able to say that plainly, rather than over-justifying it, will land better with a grader than pretending it was purely an optimal engineering call.

## 5.2 What is Google ADK, and what does it actually add?

ADK ("Agent Development Kit") is Google's own framework for building LLM agents, conceptually parallel to LangGraph but from Google, and built to pair naturally with Gemini models. The key pieces used here (`app/agent.py`):

- **`LlmAgent`** — the agent itself: a model, an instruction (system prompt), and a list of tools. ADK automatically converts each Python function passed as a tool into a callable "function tool" using its type hints and docstring — meaning the docstrings in `app/tools.py` aren't just documentation for humans, they're the literal source ADK reads to generate each tool's schema for the model.
- **`Runner`** + **`InMemorySessionService`** — the machinery that actually executes one agent run: given a session and a message, it lets the `LlmAgent` decide (call a tool? answer directly?) and streams back events until it's done.
- **`RunConfig(max_llm_calls=4)`** and a **20-second timeout** — the same two safety nets Agent System A has (`MAX_ITERATIONS`/`MAX_TOOL_STEPS` there), expressed through ADK's own configuration surface instead of LangGraph's. This satisfies the assignment's "iteration limits on every agent loop" and "timeouts on every external call" requirements for *this* service too, not just A.

**Why a throwaway session per request** (`session_id = request_id`, a fresh UUID every call): Agent System B intentionally does **not** hold a conversation the way Agent System A does. Each call is a single, bounded, stateless task ("validate this," "look up that") — there's no reason for B to remember your previous question, and building persistent session storage for it would be solving a problem that doesn't exist here. This is a deliberate, stated design decision (see the module docstring in `app/agent.py`), not an oversight.

## 5.3 The two tools, and why they need zero AI inside them

`app/tools.py`:

- **`validate_payload_tool(schema_name, payload)`**: loads the real `.schema` file from `data/raw_docs/json_schemas/`, runs it through `jsonschema.Draft7Validator` (a standard, well-tested Python JSON Schema validation library), and returns exactly which fields are wrong and why. **No LLM is involved in the validation itself** — this is precise, deterministic, and reproducible. The LLM's only job (in `agent.py`) is deciding *that* this tool should be called, with what arguments, and phrasing the result back in natural language.
- **`lookup_error_tool(error_type)`**: a plain dictionary lookup against `app/data.py`'s `ERROR_TYPES` table, which was **transcribed directly from the spec's own error table** (section 6.6.5.4), not generated or paraphrased by an LLM at any point. This matters for trustworthiness — you can literally diff this dictionary against the spec table to verify it's correct, the same way you'd audit a lookup table in any regular software project.

Both functions are reused, unchanged, by two different entry points in `app/main.py`: the raw `/validate-payload` and `/lookup-error` endpoints (structured input, no LLM, kept available for any caller that wants to skip the agent-routing layer entirely) and the ADK agent (`/agent/ask`, natural language in, natural language out). One implementation, two ways to call it — not two copies of the logic that could drift out of sync.

## 5.4 Why an encoding bug caused a pytest failure (and the general lesson)

Real story, worth knowing precisely: your `pytest` run on Windows failed on `test_schema_files_exist_and_parse` with a `UnicodeDecodeError`. Root cause: `factsheet.schema` contains UTF-8 "curly quote" characters (`“ ”`), and the code was calling `schema_path.read_text()` with **no explicit encoding**. Python's `read_text()` without an explicit encoding falls back to the operating system's default locale encoding — on Windows, that's `cp1252`, which can't decode those characters; on Linux/Mac (including inside Docker), the default is already UTF-8, so this bug was invisible there. Fixed by adding `encoding="utf-8"` explicitly everywhere a schema file is read. **General lesson worth remembering**: never rely on a platform's default text encoding for reading files that might contain non-ASCII characters — always state the encoding explicitly, because "works on my machine" (Linux/Mac) can silently fail on a teammate's Windows machine.

---

# Part 6 — The MCP Server: what "MCP" actually means

## 6.1 What is the Model Context Protocol?

MCP is a standard way for an AI agent to **discover** a menu of available tools from a separate service, and **call** them — regardless of what programming language or AI framework built either side. Analogy: it's like a restaurant menu with a QR code — any customer (any agent framework: LangGraph, ADK, or something else entirely) can scan the same menu and place an order, without needing to know anything about how the kitchen (the tool's actual implementation) works internally. The "menu" here is each tool's name, its parameters, and its docstring — which `services/mcp-server/server.py` exposes automatically for every function decorated with `@mcp.tool()`, using the `FastMCP` library.

## 6.2 Why run it as its own container, instead of just importing the functions?

Same separation-of-concerns logic as Agent System B, plus one more concrete benefit: any *other* consumer — Agent System A, the evaluation scripts (`evaluation/evaluate_multi_agent.py` connects to the exact same running `mcp-server`), or a future third agent system — can call the exact same tool implementations over the network without duplicating any code. There's exactly one place that knows how to search protocol rules or ingest a document, and everyone who needs that capability talks to it the same way.

## 6.3 Transport: streamable-HTTP, not stdio

MCP servers can run two ways: as a subprocess your program starts and talks to over stdin/stdout ("stdio transport" — simple, but tightly coupled, same-machine only), or as a standalone network server ("streamable-HTTP transport" — exactly what this project uses, `mcp.run(transport="streamable-http")`). Agent System A connects to it via `MultiServerMCPClient` pointed at `http://mcp-server:8001/mcp` (the container's name and port, resolved by Docker's internal DNS) — a genuine network call to a separately-running, separately-deployable service, consistent with the architecture's overall philosophy.

---

# Part 7 — The frontend: `chatbot-ui`

Plain static HTML, CSS, and JavaScript — deliberately **no framework** (no React, no build step). For a project of this scope, a framework would add build tooling complexity without adding much real capability the assignment needs; a single static `index.html` served by a lightweight nginx container is simpler to build, deploy, and reason about.

Key pieces:
- **Sidebar**: session list (conversations) + a "Knowledge Base" tab toggle.
- **Main chat window**: message history, the streaming "thinking" indicator (Part 4.8), the input box.
- **Document chips**: shows currently-uploaded documents for the open conversation, with a delete (✕) button per chip.
- **Knowledge Base panel**: list/upload/delete for global documents, a completely separate view from any one conversation.

**CORS** (`app.add_middleware(CORSMiddleware, ...)` in `api/main.py`): because the frontend (`http://localhost:8080`) and the backend (`http://localhost:8000`) are different "origins" (different ports count as different origins to a browser's security model), the browser blocks cross-origin requests by default unless the server explicitly allows them. `FRONTEND_ORIGIN` in the backend's config is exactly that explicit allowlist entry.

---

# Part 8 — Two full worked examples, tracing every hop

## 8.1 "What does NODE_UNREACHABLE mean?"

1. Browser sends `POST /conversations/{id}/messages/stream` with the question.
2. `_run_graph` starts the LangGraph graph. `InputGuard` passes (not empty).
3. `Supervisor` runs: the message contains `NODE_UNREACHABLE`, matching `_ERROR_TOKEN_PATTERN` — routed straight to `DiagnosticsSpecialist`, **no LLM router call needed** for this one.
4. `DiagnosticsSpecialist` (via `create_agent_node`) detects the reply language (English), calls its one tool, `ask_diagnostics_agent`, passing the question through almost verbatim.
5. That tool makes an HTTP call to `agent-system-b`'s `POST /agent/ask`.
6. Inside Agent System B: the ADK `LlmAgent` reads the question, decides to call `lookup_error_tool("NODE_UNREACHABLE")` — a plain dictionary lookup, no AI — gets back `{"found": true, "level": "CRITICAL", ...}`, and writes a natural-language sentence describing it, in English (per its own language instruction).
7. That reply travels back up: Agent System B → the tool result in Agent System A → `DiagnosticsSpecialist` relays it as the final answer (no more tool calls, so no more looping) → back to `Supervisor` (recognizes "AI message, no tool_calls" → `FINISH` immediately, no LLM call) → `OutputGuard` (checks for placeholder text — clean) → `END`.
8. Throughout steps 3–7, each node's completion streamed a `data: {"status": "..."}` SSE frame to the browser live; the final frame carries the complete answer text.

## 8.2 "Quels sont les champs requis pour un `CustomZorvaxStatus`?" (with a globally-uploaded custom schema)

1. `InputGuard` passes. No error-token pattern match, so `Supervisor` calls the LLM router — classifies this as `SchemaSpecialist` (a JSON-structure question, even though "Zorvax" isn't standard vocabulary, per the router's explicit instruction to prefer a specialist over `FINISH` when a question mixes an unfamiliar proper noun with technical-sounding content).
2. `SchemaSpecialist` detects the reply language (French, via `langdetect`), injects the language reminder onto its working copy of the last message, and calls `search_json_schemas`, **always passing `conversation_id`** (per the current design — see 3.5's trade-off).
3. `get_retriever()` builds a filter matching: this conversation's own uploads, **or** anything with no `conversation_id` at all — which includes both the official spec schemas *and* the globally-uploaded `CustomZorvaxStatus` schema (since a global upload has no `conversation_id` tag, per 3.7). Dense-only search (not the hybrid pipeline, per the trade-off in 3.5).
4. The relevant chunk (the `CustomZorvaxStatus` schema's required-fields chunk) comes back; `SchemaSpecialist` writes the final answer — French prose, with the JSON field names (`zorvaxId`, `maxRetryCount`, `heartbeatIntervalSeconds`) left untranslated per its language instruction.
5. Same `Supervisor` → `OutputGuard` → `END` tail as before.

---

# Part 9 — Every real decision and trade-off, as a reference table

| Decision | Alternative(s) considered | Why chosen | Real cost |
|---|---|---|---|
| 5 separate containers | One monolithic app | Assignment requirement; independent scaling/failure isolation; mirrors real multi-team engineering | More moving parts, more network hops, more places to configure |
| MCP server as its own service | Import tool functions directly into Agent System A | Assignment requirement; lets any framework/language call the same tools without code duplication | Extra network hop, extra container |
| Two agent systems (A and B) | One system with all logic | Assignment requirement (two frameworks, real network call); B's deterministic job is architecturally distinct from A's open-ended job | Extra container, extra timeout to manage, extra latency; honestly not required for a project this size on pure engineering merits alone |
| Hybrid (BM25+dense+rerank) retrieval when possible | Dense-only always | Objectively higher retrieval quality — catches exact keyword matches BM25 gets and embeddings can miss | Currently only used with a plain query (no `conversation_id`), which — because of the always-pass-`conversation_id` fix — means it's not actually exercised by any real query anymore. See 3.5. |
| Always pass `conversation_id` on every search | Only pass it as a fallback retry | Makes global + per-conversation uploads reliably discoverable on the *first* search attempt | Silently downgrades every query to the simpler dense-only retrieval path (the cost above) |
| JSON-schema-aware structural chunking | One generic text splitter for everything | Schemas are structured data — field/definition-level chunks give far more precise search results than arbitrary character-count cuts | More code to maintain (two chunking strategies instead of one) |
| SmallTalk vs. FINISH as two separate routes | One "off-topic" bucket for everything non-technical | Greetings deserve a warm reply, not a scope-decline; a single bucket can't tell them apart | One more LLM call path to maintain (the SmallTalk responder) |
| Deterministic regex override for error-code tokens | Trust the LLM router for every classification | The small router model was empirically unreliable on exactly this pattern; a regex is 100% precise where it applies | Only helps for the specific `ALL_CAPS_WORD` pattern — doesn't generalize to other ambiguous cases |
| Step-level SSE streaming | True token-by-token streaming | The ReAct loop needs the full response before checking `tool_calls`; restructuring it is risky this close to the deadline | Less granular than word-by-word streaming — but a real, working, incremental stream nonetheless |
| `langdetect` deterministic language detection | Trust the LLM to infer the reply language from context | The prompt-instruction-only approach broke down in multi-language conversations (real, confirmed bug) | Not perfect on very short text (skipped under 4 characters); adds a dependency |
| Two separate databases (LangGraph checkpointer + `conversations.db`) | One shared database for everything | Checkpointer schema is framework-owned; keeping it separate avoids coupling our code to LangGraph's internal storage format | Two SQLite files to reason about instead of one |
| Global vs. per-conversation uploads as two separate DB tables | One `documents` table with a nullable `conversation_id` column | A stray NULL/empty-string check could accidentally leak scope; two tables make the two upload types structurally impossible to confuse | Slightly more code (two sets of CRUD functions instead of one parameterized set) |
| Groq primary + Gemini fallback chain | Single provider, no fallback | Resilience against rate limits and provider outages, and specifically against the Aug 16 2026 Groq model deprecations | Extra complexity in `build_specialist_chain`; a removed third (Ollama) fallback shows this chain needs active maintenance, not "set and forget" |
| Plain static HTML/CSS/JS frontend | React or another framework | Simpler build/deploy for this project's scope; no build step needed | Less structure for a much larger frontend, but appropriate here |
| `_run_graph` as one shared generator for both message endpoints | Separate `ainvoke`/`astream` calls per endpoint (the original design) | Removes duplicated graph-invocation logic — a fix now only needs to happen in one place | None significant — this was a pure improvement, refactored this week specifically because you noticed the duplication |

---

# Part 10 — Known limitations, stated plainly

Say these out loud to yourself before Friday — being able to state a limitation clearly and explain *why* it exists is much stronger than being caught by a question about it.

1. **Deleting a document doesn't erase a conversation's memory of it.** The deletion mechanism itself (removing chunks from Qdrant) works correctly — but a conversation that already discussed that content keeps its own saved answer in LangGraph's checkpointed history, independent of what's currently searchable. Only *new* searches (in that or any other conversation) are affected by the deletion.
2. **The hybrid+reranked retrieval pipeline is effectively unused in current behavior.** Making uploads reliably discoverable required always passing `conversation_id`, which always routes to the simpler dense-only retrieval path. See Part 3.5 for the full explanation and a concrete recommendation to turn this into an evaluation experiment.
3. **14 of 22 spec diagrams have zero text representation in the RAG corpus.** No image/vision pipeline exists despite a stale banner claiming otherwise in `run_ingestion.py`. See the exact file list in Part 3.6.
4. **True token-by-token streaming isn't implemented**, by deliberate choice — the ReAct tool-calling loop's synchronous `.invoke()` calls need the complete response to check `tool_calls`, and restructuring that safely this close to the deadline wasn't worth the risk for a requirement the current step-level streaming already satisfies.
5. **The small router model occasionally misroutes** grammatically unusual or heavily mixed-language questions, despite the hardened prompt and the deterministic regex safety net for error codes — an inherent limitation of using a small, free-tier model for classification rather than a larger, slower, costlier one.
6. **Free-tier rate limits are a real risk under evaluation-scale load** — confirmed by your own Groq dashboard screenshots showing usage approaching the limit during ordinary manual testing, let alone a batch evaluation run.
7. **`InputGuard`/`OutputGuard` are shallow, pattern-based checks** (empty-message check; two literal hallucination-marker strings), not a comprehensive content-safety system — an appropriate scope for an internal technical assistant, but should be described accurately rather than oversold.
8. **No HTTPS or authentication between services** — fine for a local `docker-compose` demo; would need to be addressed before any real deployment.
9. **`langdetect` skips detection for very short text** (under 4 characters) and can occasionally misclassify short ambiguous strings (e.g. "hi" as Swahili in testing) — mitigated by the fact that SmallTalk replies (where short messages are most common) use a separate, already-reliable mechanism rather than this detector.

---

# Part 11 — Testing & evaluation: what exists, and what to do next

## 11.1 What already exists

- **`services/agent-system-b/tests/`** — 16 pytest tests (`test_agent.py`, `test_tools.py`), covering both deterministic tools (validation logic, error lookup, case-insensitivity, error-count-matches-spec) and the ADK agent's basic wiring (imports correctly, has both tools, has an instruction and model). All 16 currently pass.
- **`evaluation/evaluate_multi_agent.py`** — a 10-question routing-accuracy harness that connects directly to a live `mcp-server` and builds the graph in-process (bypassing the HTTP API so internal state like `state["next"]` and `state["iterations"]` is directly inspectable), comparing each question's actual route against an `expected_route`. Covers Protocol routing, Schema routing, a multi-step question, an out-of-scope question, an adversarial prompt-injection attempt, and both guardrails.
- **`evaluation/test_guardrails.py`** — a smaller, guardrail-focused test set.
- **`evaluation/results/multi_agent_eval.json`** — prior run results from an earlier version of the system.

## 11.2 What's still needed, explained in beginner terms

`docs/EVALUATION.md` lays out six required sections. Here's each one explained plainly:

1. **Test set** — 15–20 questions with a known-correct answer and which spec section/schema it should come from, extending the existing 10-query set. This is your ground truth to measure everything else against.
2. **Retrieval metrics** — how good is the *search*, independent of the LLM's writing:
   - **Precision@3**: of the top 3 chunks returned, what fraction were actually relevant?
   - **Recall@3**: of all the truly relevant chunks that exist, what fraction did the top 3 catch?
   - **MRR** (Mean Reciprocal Rank): on average, how high up did the *first* relevant result rank? (1st place = 1.0, 2nd place = 0.5, etc.)
   - **NDCG**: like Precision, but rewards putting the *best* results higher up, not just having them present somewhere in the top 3 — worth including specifically because your pipeline reranks results, and NDCG is the metric sensitive to ranking order, which a reranker directly affects.
3. **Generation evaluation** — is the *written answer* actually good, given what was retrieved? RAGAS is a library with pre-built metrics for exactly this (faithfulness — does the answer only say things the retrieved context supports; correctness; relevance). An LLM-as-judge rubric (asking a strong model to score each answer against a rubric) is the fallback if RAGAS proves impractical to set up in time.
4. **Agent/routing evaluation** — extend `evaluate_multi_agent.py`'s existing methodology to also cover Agent System B's routing (does `ask_diagnostics_agent` get called correctly, does B's own internal tool choice match expectations), plus explicit tool-selection correctness metrics.
5. **Two configuration comparisons** — run the same test set through two different settings and compare the retrieval metrics. The write-up already suggests chunk size (500 vs. 1000) and retrieval depth (top-K 3 vs. 5). **Strongly consider adding (or substituting) the hybrid-vs-dense-only comparison found in Part 3.5** — it's real, working code already in the repo, directly tied to an actual trade-off in the deployed system, and would produce a genuinely informative result rather than an arbitrary parameter sweep.
6. **Failure case analysis** (`docs/FAILURE_ANALYSIS.md`) — see 11.3 below; you already have three real, documented ones ready to write up.

## 11.3 Three real failure cases, ready to write up (found and fixed this week)

These are genuine failures from actual testing this week, already root-caused and fixed — exactly the format `docs/FAILURE_ANALYSIS.md` asks for:

**Failure 1 — Cross-language diagnostics answers**
- **Query**: "What does the error type BANANA_ERROR mean?" (asked in English)
- **Expected**: An English reply
- **Actual**: A German reply
- **Classification**: Prompt failure (Agent System B's ADK agent instruction had no language-matching rule at all, while Agent System A's specialists already did — an inconsistency between the two systems, not a model limitation)
- **Fix**: Added the same explicit language-matching instruction to Agent System B's `LlmAgent` instruction that Agent System A's specialists already had.

**Failure 2 — Document deletion silently not deleting**
- **Query**: Upload a document, ask about it (correct answer), delete it, ask again
- **Expected**: The system should no longer find/answer about the deleted content
- **Actual**: It kept answering as if the document were still present
- **Classification**: Design failure (an ID-generation mismatch between the SQLite metadata row and the actual Qdrant chunk tags — two different random IDs were generated for what should have been the same document)
- **Fix**: Made both the database layer and the vector-store layer use the exact same document ID, generated once at upload time.

**Failure 3 — Small-talk hard-blocked by the input guardrail**
- **Query**: "Hi"
- **Expected**: A friendly greeting reply
- **Actual**: A guardrail block (`GUARDRAIL_BLOCK: The question does not seem related to VDA 5050...`)
- **Classification**: Design failure (a static keyword-list guardrail can't distinguish a harmless greeting from a genuinely off-topic question — that distinction requires understanding, not pattern matching)
- **Fix**: Removed topic-relevance judgment from the input guardrail entirely; added a `SmallTalk` classification to the Supervisor's routing so greetings get a warm, on-topic-redirecting reply instead of a block.

**Failure 4 — The hybrid+reranker retrieval pipeline is never actually used in production**
- **Query**: any query at all, not a specific one — this is a systemic finding, not a single bad answer
- **Expected**: every search goes through the full hybrid (dense + BM25) + cross-encoder reranker pipeline built in `core/retriever.py`
- **Actual**: `get_retriever()` only builds that full pipeline when `conversation_id` is NOT passed. When the upload-scoping fix made every specialist pass `conversation_id` on every search (not just as a fallback), every single search — including questions with zero uploaded documents involved — silently dropped to the dense-only branch, with no error, warning, or visible symptom
- **Classification**: Design failure (a correctness fix — making per-conversation uploads discoverable — was implemented as an all-or-nothing branch in `get_retriever()` rather than a merge of both retrieval paths, so fixing one requirement silently regressed a different one; not a model or prompt issue)
- **Measured impact**: see `evaluation/04_config_comparisons/results/comparison_a_hybrid_vs_dense_latest.json` for the actual Precision@3/Recall@3/NDCG@3 deltas between the intended (hybrid+rerank) and deployed (dense-only) configurations, run against the same test set
- **Fix (if attempted)**: deliberately deferred — see Part 3.5 for the scoped, contained fix (merge the hybrid-over-base-corpus path with a dense-only lookup over conversation-scoped chunks, rather than branching all-or-nothing), and why it wasn't made this close to the deadline. Documented as a known limitation with a stated correct fix, and turned into the evaluation suite's primary configuration comparison instead (see Part 13) — both are legitimate, defensible outcomes for a real trade-off found under time pressure.

A fifth candidate, if you want one more: the Windows `UnicodeDecodeError` in the pytest suite (Part 5.4) — classified as a **model/environment failure** (platform-dependent default text encoding), distinct in character from the four above (prompt/design failures), which would give your failure analysis section good variety across failure types.

# Part 12 — Quick-reference cheat sheet for Friday

**"Why did you split this into two agent systems instead of one?"**
The assignment requires two independently-built systems on different frameworks talking over a real network call — that's the primary reason. Beyond that, B's job (deterministic validation/lookup) is architecturally distinct from A's job (open-ended retrieval and conversation), and splitting them means A degrades gracefully instead of crashing if B goes down. Honestly, for a project this size, one well-organized system would also have worked fine on pure engineering merit — the split exists mainly to satisfy the assignment's integration requirement.

**"What is MCP and why use it?"**
A standard protocol letting any AI agent framework discover and call a menu of tools from a separate service, without needing to know how that service is implemented internally. Used here so the tool implementations (search, ingest, delete) live in exactly one place, callable by Agent System A, the evaluation scripts, or any future consumer, over the network rather than as a Python import.

**"What is RAG and why not just ask the LLM directly?"**
Retrieval-Augmented Generation: search your actual documents first, then have the LLM answer using only what was found, rather than trusting its trained-in memory (which wasn't trained on your specific uploaded documents, and shouldn't be trusted to recite a 2,175-line spec accurately from memory anyway).

**"Does your RAG system cover the whole spec?"**
The full markdown text and all 8 JSON schemas, yes. 14 of the 22 diagram images have zero text representation and are invisible to search — no vision pipeline exists despite a leftover comment claiming otherwise.

**"Why SSE streaming per step instead of per word?"**
Every specialist needs the model's complete response before it can check whether it wants to call a tool — a half-streamed response can't answer that. Restructuring the whole tool-calling loop to support token streaming was too risky this close to the deadline for a requirement step-level streaming already satisfies.

**"What LLMs are you using and why?"**
Groq-hosted open models (`gpt-oss-20b` for routing, `gpt-oss-120b` for answering) as primary, with Gemini flash-lite models as automatic fallback. Chosen because the two previous models were both being decommissioned by Groq on August 16, 2026, and these are Groq's own recommended free-tier replacements.

**"What would you do differently with more time?"**
Fix the retrieval trade-off in Part 3.5 (get hybrid+reranked search working even when a conversation_id is passed), add a real vision/image pipeline for the 14 uncovered diagrams, and restructure the ReAct loop to support genuine token-level streaming.

**"What's the biggest known limitation?"**
Probably the retrieval trade-off (Part 3.5) — it's subtle, self-inflicted by a correct fix to a different bug, and currently means the more sophisticated retrieval pipeline in the codebase isn't actually being exercised by real traffic.

---

# Part 13 — The evaluation suite (`evaluation/`), what it covers and how to defend it

Built to close the one real gap identified against the requirements: `docs/EVALUATION.md` was a skeleton, and `evaluate_multi_agent.py` alone only covered routing accuracy for Agent A. `evaluation/` now has five parts:

## 13.1 `00_shared/` — the test sets everything else reads from

Ground truth for retrieval (`test_set_retrieval.json`, 18 questions) is deliberately **(source file, required keywords)**, not a fixed chunk ID — a chunk ID from a 500-char naive split means nothing in a header-aware split, and the whole point of Part 13.2 is comparing across chunking strategies. `test_set_agent.json` (15 cases) and `test_set_generation.json` (10 cases, including two deliberately unanswerable questions to test grounded refusal) round out the ground truth. `metrics.py` implements Precision@K/Recall@K/MRR/NDCG@K, a routing confusion-matrix builder, and a results writer that timestamps every run instead of overwriting the last one.

**Everything below has actually been run** — full numbers, tables, and
per-question detail are in `docs/EVALUATION.md`, cited to the specific
timestamped result file each number came from. This section is the
"what it means" companion; that one is the "what it says" report.

## 13.2 `01_retrieval_progressive/` — proving the current RAG configuration, not just running it once

This is the direct answer to "why this chunk size, why this embedding strategy" — instead of asserting it, it's measured across seven real configurations, in the order an engineer would actually arrive at them:

1. Naive fixed-size chunking, 500 chars, no overlap (the "if you did nothing deliberate" baseline) — **Precision@3 = 0.556**
2. Same, + 100-char overlap (does overlap fix boundary cases?) — **0.519**, a real regression: more near-duplicate chunks from overlap, no new information, confused ranking
3. Same, 1000 chars + overlap (does more context per chunk help or dilute relevance?) — **0.519** precision (unchanged), but MRR/NDCG improved (0.833) — bigger chunks ranked *better* without being found *more often*
4. The real structure-aware chunking (`core/ingestion.py` — Markdown header splitting, JSON-schema parent-child splitting), dense-only retrieval — **0.574**, best of the four dense configs, using **371 chunks vs. up to 1015** for the naive configs
5. Same chunks, + BM25 hybrid retrieval — **0.481**, a real precision dip; raw hybrid merge alone isn't obviously better than dense-only
6. Same chunks, + cross-encoder reranker — the **intended** design — **0.648**, best on every metric; the reranker is what actually turns "more candidates" into "better top-3," not the hybrid merge by itself
7. Same chunks, through the literal `get_retriever(conversation_id=...)` code path — what's **actually deployed** — **0.574**, worse than the intended design on every metric (see Failure 4 above)

Every config embeds into its own throwaway `eval_cfg_*` Qdrant collection (never touches the production `vda5050_baseline` collection), gets scored against the same 18 questions, and gets its own timestamped results file — plus a consolidated `progression_summary` with an auto-generated (measured-delta-driven, not asserted) verdict at each step.

**A methodology note worth knowing cold for the defense:** an earlier run had a bug where Recall@K and MRR for configs 5 and 7 were measurement artifacts, not real values — those two retrievers' raw `.invoke()` calls returned far more candidates (10-19) than the dense configs' fixed 3, and Recall's denominator was accidentally computed against however many candidates came back, not a fixed comparison point. Found by noticing config 5's Recall@3 (0.244) didn't match its otherwise-reasonable Precision@3 (0.481) — a ratio that shouldn't be possible if both were measured the same way. Fixed by truncating every retriever's output to exactly K before scoring; **re-run and confirmed**: config 5's Recall@3 is now a believable 0.722, and — the more important result — config 7 (the real deployed path) now scores *identically* to config 4 on all four metrics, which is itself proof that the separate retriever.py bug (below) is fixed. Full before/after: `docs/EVALUATION.md` §7.

**Confirmed final numbers** (after both fixes, re-run): config 6 (hybrid+rerank, intended) is best on every metric — P=0.648, R=0.889, MRR=0.861, NDCG=0.868. Config 5 (hybrid, no reranker) is a genuine, confirmed regression below dense-only alone (P=0.481, R=0.722) — a useful negative result: BM25 pulls in lexically-similar-but-weak chunks, and only the reranker sorts that out. Config 7 (deployed) now matches config 4 (dense-only) exactly — meaning the only remaining gap between what's deployed and what's possible is the known, intentional "no hybrid+rerank" trade-off, not an extra accidental regression on top of it.

## 13.3 `02_generation/` — faithfulness, correctness, relevance

LLM-judge rubric (single combined call per question, to keep quota cost low), defaulting to a **local Ollama `llama3.1:8b` judge** for both the answer-generation step and the judging step — zero Groq/Gemini quota used, addressing the free-tier pressure noted in Part 4.7. Deliberately retrieves through the same `conversation_id`-set path the real specialists use, so this measures what users actually receive, consistent with Part 13.2's numbers rather than grading an idealized pipeline nobody is served. **Result: 4.9/4.8/5.0 (faithfulness/correctness/relevance)**, both deliberately-unanswerable test questions correctly refused rather than guessed at — though see `docs/EVALUATION.md` §3 for why a same-model generate-and-judge setup deserves a cross-check with a second judge backend before leaning on the exact numbers in a defense.

## 13.4 `03_agent_eval/` — the six things Session 11 says to measure on an agent

Routing accuracy as a confusion matrix (not just a pass rate), tool-selection accuracy split into name-correctness and invented-tool detection (and broken down **per tool**, since an averaged score can hide one badly-performing tool), in-order trajectory match, fact-checklist task completion, and step efficiency (actual steps ÷ minimum steps). Extends the original 10-case set with Agent System B routing/tool cases (`ask_diagnostics_agent` → `validate_payload_tool`/`lookup_error_tool`), a small-talk case, and a French-language case — the last one is real, cheap evidence for "why this embedding model" (`BAAI/bge-m3` is multilingual by design) and ties directly to Failure 1 (Part 11.3). **Result: 80% routing accuracy (12/15), 0 invented tool calls, 90% fact-coverage task completion** — the two routing "misses" and the step-efficiency outlier are worth reading individually, not just as one number: `docs/EVALUATION.md` §4 walks through why `a03`/`a06`/`a11` are more test-labeling nuance than system bugs, while the `a03` step-efficiency number (11 steps vs. a minimum of 2) is a real thing worth tracing before the defense.

## 13.5 `04_config_comparisons/` — the two required numbers

Pulls the project's two required configuration comparisons directly from 13.2's code (not a reimplementation that could drift): **(A)** hybrid+rerank (intended) vs. dense-only (deployed) — Precision@3 0.648 vs. 0.574, a confirmed **−0.074** drop, Recall@3 −0.056 — real and worth documenting, but notably smaller than it first appeared before the k=10 bug fix (that bug had been stacking an accidental extra regression on top of the real, intentional trade-off) — and **(B)** top-K=3 vs. top-K=5 on the best retriever — K=5 wins on recall (+0.056) for negligible precision cost, a defensible but not urgent case for bumping K. Chunk size (500 vs. 1000) was deliberately NOT used as one of the two required comparisons — `CHUNK_SIZE`/`CHUNK_OVERLAP` only govern the plain-text fallback splitter, not the real Markdown/JSON-schema structural chunking, so that comparison would move the needle far less than either of the two actually chosen; that reasoning is itself worth stating if asked why chunk size wasn't one of the two.

---
