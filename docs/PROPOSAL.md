**inmind.academy**

by inmind.ai

Machine Learning Track --- Final Project

**VDA 5050 FLEET OPERATIONS SYSTEM**

**FINAL PROJECT PROPOSAL**

*Topic and Architecture Plan*

By:

**Mario AOUAD**

Instructor:

**Mr. Dani AZZAM**

DATE

8/9/2026

Internship at Inmind.academy

By Inmind.ai

**Table of Contents**

**1. Project Overview**

**1.1 Problem Statement and Users**

Coordinating a mixed fleet of automated guided vehicles (AGVs/AMRs)
requires every vehicle, regardless of manufacturer, to speak a common
language over MQTT --- this is what the VDA 5050 standard defines. In
practice, engineers integrating a new robot, debugging a fleet
controller, or validating a message payload spend real time
cross-referencing a long specification document and a set of JSON schema
files by hand. The system proposed here is a production-shaped assistant
for that exact workflow: an operations engineer asks a question or
submits a payload, and the system answers from the real standard ---
grounded, cited to source, and, where the task is validation rather than
explanation, checked deterministically rather than guessed at by a
language model.

**1.2 What We Are Building**

Two cooperating agent systems, each owning a distinct responsibility,
communicating over the network rather than as parts of one program:

-   **Agent System A --- the Protocol & Schema Oracle (LangGraph,
    Python).** A supervisor-routed multi-agent system that answers
    conceptual questions about VDA 5050 protocol rules and JSON schema
    structure, grounded in retrieval over the real specification. This
    is the primary system and the one end users interact with directly,
    through a chatbot UI with persistent, multi-session conversation
    memory.

-   **Agent System B --- the Fleet Diagnostics & Validation Agent
    (Google ADK).** An independent service, built on a different
    framework, that Agent System A calls over HTTP when a task needs
    deterministic, structured handling rather than conceptual Q&A. As of
    the final submission, that HTTP call goes to Agent System B\'s own
    ADK agent endpoint (POST /agent/ask) rather than to its raw
    validation/lookup endpoints directly --- Agent System B\'s LlmAgent
    receives the user\'s diagnostics question in natural language and
    decides for itself whether it needs schema validation or error-code
    lookup, then calls the matching deterministic tool internally. This
    mirrors exactly the production pattern the assignment describes ---
    one team\'s agent calling a distinct service owned and built
    independently, across a real network boundary, with that service
    making its own tool-use decisions rather than being told which tool
    to call.

Both are backed by a shared MCP server exposing the retrieval and
document-management tools, a Qdrant vector database running as its own
container, and a chatbot frontend --- five containers total, started
with one docker-compose command, matching the required architecture in
Section 2.4 below.

**1.3 Why This Domain, and Continuity With Prior Work**

This is a continuation of my Sub-Project 1 (baseline RAG) and
Sub-Project 2 (multi-agent + MCP) work on the same VDA 5050 domain,
extended since then into a persistent chatbot with document upload ---
already covered in a separate progress report. That existing system
already provides real, working pieces of this final project: a
validated-routing supervisor, an MCP server with two working tools,
input/output guardrails wired as graph nodes, a hybrid retrieval
pipeline, and a FastAPI backend with session persistence. This proposal
is honest about which parts of the rubric that existing work already
satisfies, and which parts --- most importantly, Agent System B, the
second network-independent service --- are net-new work for this
project.

**2. Architecture Plan**

This section addresses every mandatory requirement in Section 2 of the
assignment brief, in the same order, so it can be checked point by
point.

**2.1 Two Independent Agent Systems**

Agent System A (LangGraph) holds the core business logic: a Supervisor
node that validates its routing decision against a known list of agent
names before using it as a graph edge, and specialists
(ProtocolSpecialist, SchemaSpecialist) with narrow system prompts and
their own tool sets, already built and evaluated in Sub-Project 2.

Agent System B (Google ADK) is a separate Python service with its own
process, its own FastAPI wrapper, and its own container. It is not
imported by Agent System A. When the Supervisor determines a question
requires payload validation or error-code diagnosis rather than
conceptual explanation, Agent System A calls a single tool
(ask_diagnostics_agent) that issues an HTTP request to Agent System
B\'s POST /agent/ask endpoint, passing the user\'s question through in
natural language, waits for a structured response (with a timeout ---
see Section 2.6), and folds the result into its final answer. Unlike
the earlier design, Agent System A no longer decides which of Agent
System B\'s two tools to call or how to structure the arguments ---
that decision now happens inside Agent System B itself, via its own
Google ADK LlmAgent (app/agent.py), which receives the natural-language
question and calls whichever of its two tools fits: a schema-validation
tool that checks a submitted JSON payload against the real VDA 5050
schema deterministically, and an error-code lookup tool that returns
the standard\'s defined meaning and handling guidance for a given error
type and level. Agent System B still exposes its raw POST
/validate-payload and POST /lookup-error endpoints directly too, for
callers that want to skip the LLM routing layer entirely and pass
already-structured arguments.

This gives the two systems a genuine reason to exist independently:
System A reasons about open-ended language and retrieval; System B
performs a bounded, deterministic task well suited to a different
framework, exactly the \'different team, different stack, network
boundary\' pattern the assignment is testing for.

**2.2 RAG Pipeline**

-   **Ingestion:** the existing pipeline already ingests the real VDA
    5050 Markdown specification and its JSON schema files from disk,
    plus user-uploaded documents through the chatbot\'s upload feature.

-   **Chunking strategy (justified):** Markdown is split by header
    hierarchy (H1/H2/H3) rather than fixed character windows, so a chunk
    corresponds to one coherent section of the spec rather than an
    arbitrary slice; oversized sections fall back to a secondary
    splitter at 1500 characters with 200-character overlap. JSON schemas
    are split by structural unit --- one chunk per top-level property
    and one per definition (node, edge, action, and so on) --- rather
    than by character count at all, because a schema\'s meaningful unit
    is a property or definition, not a line count. A general fallback
    splitter (1000 characters, 100-character overlap) exists for any
    other file type. This is deliberately structure-aware rather than
    naive fixed-size chunking, because questions in this domain are
    almost always about one section or one field, and structure-aware
    chunks keep that unit intact instead of splitting it across two
    chunks.

-   **Embedding model (justified):** BAAI/bge-m3, chosen for strong
    retrieval performance on technical and structured text, a long
    context window suited to schema chunks with many nested properties,
    and multilingual support in case the corpus is ever extended beyond
    English-language specification text.

-   **Vector database:** Qdrant. The current implementation runs Qdrant
    embedded (local file, single-process); for this project it moves to
    a standalone Qdrant server running in its own container (vector-db),
    connected over the network by both the MCP server and, if needed,
    Agent System B --- matching the required container layout and
    removing the single-process file-lock constraint the embedded mode
    currently has.

-   **Metadata filtering:** already implemented --- retrieval can filter
    by file_type (protocol vs. schema) and by conversation_id (so a
    conversation\'s uploaded documents are searchable only within that
    conversation), combined with the base corpus via an OR filter.

-   **Grounded generation prompt:** each specialist\'s system prompt
    will be extended with an explicit grounding instruction: answer only
    from retrieved context, and state plainly when the retrieved context
    does not contain the answer, rather than filling the gap from
    general knowledge. This is a concrete, small change to existing
    prompts, not new infrastructure.

**2.3 MCP Server**

The existing FastMCP server already exposes two working retrieval tools
with real docstrings (search_protocol_rules, search_json_schemas), plus
document-management tools added for the chatbot\'s upload feature
(ingest_document, delete_document, delete_conversation_documents). For
this project it moves into its own Docker container and is called by
Agent System A over the network rather than as a stdio subprocess,
satisfying the \'runs in its own container, callable by at least one
agent system\' requirement directly. It remains usable by Cursor/Claude
Code as well, preserving the two-consumers-one-server pattern already
demonstrated in Sub-Project 2.

**2.4 Dockerized Microservice Architecture**

Five containers, each with its own Dockerfile and requirements file,
started together with a single docker-compose command:

  -----------------------------------------------------------------------
  **Container**       **What It Runs**
  ------------------- ---------------------------------------------------
  agent-system-a      LangGraph primary agent system (Supervisor +
                      specialists) wrapped in FastAPI

  agent-system-b      Google ADK Fleet Diagnostics & Validation agent,
                      its own FastAPI wrapper

  mcp-server          FastMCP server: retrieval + document-management
                      tools

  vector-db           Qdrant, running as a standalone server rather than
                      embedded

  chatbot-ui          The existing frontend, served independently rather
                      than bundled into agent-system-a
  -----------------------------------------------------------------------

The chatbot frontend currently is served directly by the FastAPI backend
via StaticFiles; for this project it is split out into its own
lightweight static-file container, decoupling it from Agent System A as
the required container list expects.

**2.5 API Layer**

-   **Chat/query endpoints:** Agent System A already exposes POST
    /conversations/{id}/messages; Agent System B exposes its own POST
    endpoint for validation/diagnostic requests.

-   **Streaming:** Agent System A\'s message endpoint now has a
    server-sent-events variant (POST /conversations/{id}/messages/stream),
    alongside the original POST /conversations/{id}/messages which still
    waits for the full result. The streaming variant uses LangGraph\'s
    astream(stream_mode="updates") to emit one event per graph step as it
    finishes (Supervisor routing, a specialist searching, the final
    answer), rather than per LLM token --- each specialist\'s own chat
    model call needs its complete response up front to check tool_calls
    and decide whether to loop through a ToolNode, which is incompatible
    with true token-level streaming without restructuring the ReAct loop
    entirely. Step-level streaming is still genuinely incremental over the
    wire; it\'s chunked by graph step instead of by token.

-   **Session management:** Agent System A already has this ---
    LangGraph\'s checkpointer persists full conversation state per
    thread_id, verified working across multiple turns and sessions.
    Agent System B\'s tasks are single-shot (validate this payload, look
    up this error) rather than conversational; it will track requests
    with a request/session identifier for traceability and auditability,
    satisfying the letter of the requirement, and I want to raise with
    you at the Q&A session whether a stateless service like this needs
    full conversational session history, or whether request-level
    tracking is the right interpretation here.

-   **Error handling and timeouts:** both services will catch internal
    exceptions and return a generic structured error response rather
    than a raw stack trace, and every external call --- including Agent
    System A\'s call into Agent System B, and any MCP tool call --- will
    carry an explicit timeout so a slow or unreachable dependency
    degrades gracefully instead of hanging the request indefinitely.

**2.6 Guardrails and Safety**

-   **Input/output guards:** already implemented and unit-tested on
    Agent System A (InputGuard, OutputGuard, wired as real graph nodes).

-   **Iteration limits:** already implemented on Agent System A\'s
    supervisor loop and each specialist\'s tool-calling loop; Agent
    System B\'s task loop will carry an equivalent bound since it is a
    new system being built for this project.

-   **Timeouts on every external call:** as above --- this explicitly
    includes the call Agent System A makes to Agent System B, not only
    third-party APIs.

-   **No hardcoded secrets:** already the project\'s practice --- a .env
    file (never committed) and a maintained .env.example, following the
    same discipline established after an earlier accidental key exposure
    during Sub-Project 2 that was caught and rotated.

**2.7 Evaluation Plan**

-   **Test set:** roughly 15--20 questions with ground-truth answers and
    the source section or schema each answer should come from, extending
    the 10-query set already built for Sub-Project 2 with new questions
    that specifically exercise Agent System B\'s routing path.

-   **Retrieval metrics:** Precision@K and Recall@K at K=3 (the
    system\'s existing default retriever depth), plus Mean Reciprocal
    Rank; NDCG as well, since the pipeline already reranks with a
    cross-encoder and NDCG is the metric that actually reflects
    reranking quality.

-   **Generation evaluation:** RAGAS (or an anchored LLM-judge rubric if
    RAGAS integration proves impractical in the time available) scoring
    faithfulness, correctness, and relevance across the full test set.

-   **Agent evaluation:** routing accuracy against expected routes,
    extending the methodology already used in Sub-Project 2 (expected
    vs. actual route, correctness, iteration count per query) to include
    the new Agent-System-B routing path, plus tool-selection correctness
    for the validation/diagnostic tools.

-   **Configuration comparisons:** at minimum two, with numbers ---
    chunk size (e.g. 500 vs. 1000 characters) and retrieval depth (top-K
    3 vs. 5) are the two most directly testable given the existing
    pipeline, each reported with the retrieval metrics above under both
    settings.

-   **Failure case analysis:** three documented failures, each
    classified as a model, prompt, or design failure with justification
    --- the same methodology already used and demonstrated in the
    Sub-Project 2 report\'s failure analysis section.

**2.8 Bonus (Time Permitting, Attempted Only After Everything Above Is
Complete)**

Given the two-week timeline, bonus work is explicitly deprioritized
behind every mandatory item above. If time remains after the mandatory
scope is complete and evaluated, a fine-tuned small classifier exposed
as a tool is the more realistic bonus target of the two offered, since
it extends work already underway (structured classification) rather than
introducing an entirely new modality like voice.

**3. Already Built vs. New for This Project**

In the interest of being precise about scope, since this determines how
much of the two weeks is genuinely available for the hardest,
highest-weighted parts of the rubric (evaluation, and the second agent
system):

  -----------------------------------------------------------------------
  **Requirement**                                    **Status**
  -------------------------------------------------- --------------------
  Agent System A (LangGraph, supervisor,             Already built
  specialists, validated routing)                    (Sub-Project 2)

  Input / output guardrails, unit-tested             Already built
                                                     (Sub-Project 2)

  MCP server with 2+ tools, real docstrings          Already built
                                                     (Sub-Project 2 +
                                                     chatbot phase)

  Hybrid RAG pipeline, structure-aware chunking,     Already built
  reranking                                          (Sub-Project 1 + 2)

  Agent System A as a FastAPI service with session   Already built
  persistence                                        (chatbot phase)

  Chatbot frontend                                   Already built
                                                     (chatbot phase) ---
                                                     being split into its
                                                     own container

  Agent System B (Google ADK), independent network   New --- not started
  service                                            

  Docker / docker-compose, all 5 containers          New --- not started

  Qdrant as a standalone containerized service (not  New --- not started
  embedded)                                          

  SSE streaming endpoint                             New --- not started

  Grounded-generation prompt instruction             New --- small prompt
                                                     change

  Full evaluation suite (RAGAS, retrieval metrics,   New --- not started
  config comparisons)                                
  -----------------------------------------------------------------------

**4. Open Questions for the Q&A Session**

Bringing specific, concrete questions rather than a general status
update, as requested:

-   Session management for Agent System B: it performs bounded,
    single-shot validation/diagnostic tasks rather than holding a
    conversation. Is request-level traceability (not full conversational
    history) an acceptable interpretation of the session-management
    requirement for a service shaped like this, or is real
    conversational session state expected regardless of the task?

-   For the A2A-vs-HTTP choice in Section 2.1: is a plain authenticated
    HTTP/REST call between Agent System A and Agent System B sufficient,
    or is the formal A2A protocol specifically expected for full marks?

-   For the chunk-size configuration comparison: is comparing retrieval
    metrics only (Precision@K/Recall@K/MRR) sufficient, or should the
    comparison also re-run generation evaluation (RAGAS) under both
    configurations?

**5. Timeline**

  -----------------------------------------------------------------------
  **Date**            **Milestone**
  ------------------- ---------------------------------------------------
  Sun 9 -- Mon 10 Aug This architecture proposal finalized and submitted

  Tue 11 -- Wed 13    Agent System B built (Google ADK); Docker/compose
  Aug                 for all 5 containers; Qdrant moved to standalone
                      containerized mode

  Thu 14 -- Fri 15    API layer completion: SSE streaming, timeouts and
  Aug                 error handling on every external call,
                      grounded-prompt update

  Sat 16 -- Sun 17    Evaluation suite: test set, retrieval metrics,
  Aug                 RAGAS/LLM-judge generation scoring, routing
                      accuracy, configuration comparisons

  Mon 18 Aug          README, EVALUATION.md, three failure-case
                      write-ups, demo script

  Wed 19 Aug          Q&A session --- open questions above

  Thu 20 Aug          Buffer, polish, recorded demo backup

  Fri 21 Aug          Final submission and presentation
  -----------------------------------------------------------------------

**6. Request for Topic and Architecture Approval**

I\'d like to continue building on the VDA 5050 domain and the
multi-agent system already evaluated in Sub-Project 2, extending it into
the two-independent-agent-system architecture this final project
requires --- adding a genuinely separate, network-independent Google ADK
service for schema validation and error diagnostics, containerizing the
full system, and building out the evaluation suite this rubric weights
most heavily.

I\'m requesting your review and approval of this topic and architecture
plan, along with any feedback on the open questions in Section 4 before
I begin implementation.
