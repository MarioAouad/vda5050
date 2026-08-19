import os
import re
from typing import Annotated, Literal, Sequence, Optional
from typing_extensions import TypedDict
import httpx
from langdetect import detect as _langdetect_detect, DetectorFactory as _LangDetectFactory
from langdetect.lang_detect_exception import LangDetectException
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

AGENT_B_URL = os.getenv("AGENT_B_URL", "http://localhost:8002")


@tool
async def ask_diagnostics_agent(query: str) -> str:
    """Forward a fleet-diagnostics question to Agent System B's own Google
    ADK agent (POST /agent/ask). Agent System B decides for itself whether
    this is a payload-validation question or an errorType lookup, calls the
    right deterministic tool internally, and returns a plain-language
    answer — you do not need to (and should not) pre-parse the question
    into schema_name/payload or error_type yourself; pass the user's
    diagnostics question through close to verbatim, including any JSON
    payload they gave you. Covers: validating a JSON payload against a real
    VDA 5050 schema (order, state, instantActions, connection,
    visualization, factsheet, zoneSet, responses), and looking up an
    errorType's defined severity level and handling guidance (e.g.
    NODE_UNREACHABLE, LOCALIZATION_ERROR)."""
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            resp = await client.post(f"{AGENT_B_URL}/agent/ask", json={"query": query})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return f"Could not reach the diagnostics agent: {type(e).__name__}: {e}"

    return data.get("reply") or "The diagnostics agent did not return a response."


def build_specialist_chain(tools=None):
    # llama-3.3-70b-versatile is ALSO decommissioned by Groq on Aug 16, 2026
    # (separate deprecation email from the llama-3.1-8b-instant one the
    # supervisor below already migrated away from) — migrated here to
    # openai/gpt-oss-120b, same pattern as the supervisor's model. Free-tier
    # quota for gpt-oss-120b is 30 RPM / 1K RPD / 8K TPM / 200K TPD, which is
    # actually the same RPD llama-3.3-70b-versatile already had (1K), so
    # this isn't a quota downgrade — and it's still backed by the Gemini
    # fallbacks below for anything Groq can't serve (rate limit or outage).
    primary = ChatGroq(model="openai/gpt-oss-120b", temperature=0.1)
    fb1 = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", temperature=0.1)
    fb2 = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0.1)
    # A local-Ollama fb3 used to sit here as a third fallback. Removed: there's
    # no Ollama service in docker-compose.yml, so inside any of these
    # containers it points at nothing, and a guaranteed-broken fallback turns
    # "Groq and Gemini both failed" into a hard crash (with_fallbacks
    # re-raises once every option is exhausted) instead of the graceful
    # degradation a fallback chain is supposed to provide. Add it back with a
    # real `ollama` service in docker-compose.yml if you want that resilience
    # layer for real.

    if tools:
        primary = primary.bind_tools(tools)
        fb1 = fb1.bind_tools(tools)
        fb2 = fb2.bind_tools(tools)

    return primary.with_fallbacks([fb1, fb2])


SUPERVISOR_PROMPT = """You are a supervisor managing a team of three specialists:
1. ProtocolSpecialist: Answers questions about VDA 5050 protocol rules, constraints, and standard definitions.
2. SchemaSpecialist: Answers questions about VDA 5050 JSON schema structures, required fields, and data types.
3. DiagnosticsSpecialist: Validates a JSON payload against the real VDA 5050 schemas, and looks up the standard's defined severity level and handling guidance for a specific errorType (e.g. "what does NODE_UNREACHABLE mean", "is this order payload valid").

The user may have uploaded custom or manufacturer-specific documents (e.g.
protocol extensions, custom schemas) that use terminology not found in the
standard VDA 5050 vocabulary. Route ANY question about protocol rules,
behavior, topics, or JSON schema/field structure to the appropriate
specialist — even if the specific terms are unfamiliar — since specialists
can search uploaded documents too, not just the base VDA 5050 spec.

Two different kinds of "not a technical question" exist, and they get
different routing:
- SmallTalk: greetings, thanks, farewells, "how are you", or similar pure
  social pleasantries with no real question in them (e.g. "hi", "hello",
  "thanks!", "bye", "ça va?"). Route these to SmallTalk.
- FINISH: a genuine question about something else entirely — general
  knowledge, other topics, opinions unrelated to VDA 5050/robotics/fleets
  (e.g. "what's your favorite pizza topping", football scores, how to cook
  pasta). Route these to FINISH.

When in doubt between a specialist and FINISH because the question mixes an
unfamiliar term with something that still sounds technical/protocol-related,
prefer routing to a specialist — the uploaded-documents note above is exactly
for that case.

The question may be written in any language, with imperfect grammar, or
mixing two languages in one sentence (e.g. a French sentence with an English
word dropped in). None of that is evidence the question is off-topic — judge
the topic on its content, not its grammar or language. If a proper noun in
the question doesn't match standard VDA 5050 vocabulary (e.g. it looks like
it could be a custom/uploaded document's name), that alone is a reason to
route to a specialist per the note above, not a reason to pick FINISH.
"""

MAX_ITERATIONS = 3
MAX_TOOL_STEPS = 3


def _as_text(content) -> str:
    """
    Normalize AIMessage.content to a plain string. Most providers return a
    str; some (certain langchain-google-genai responses in particular) can
    return a list of content parts instead — e.g. [{"type": "text",
    "text": "..."}] — which breaks any code that calls .lower()/.startswith()
    on it directly, and renders as "[object Object]" if it reaches the
    frontend unconverted. Pinning exact package versions (see
    requirements.txt) should prevent this in practice, but every place that
    treats .content as a string goes through this helper as cheap insurance.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


# Deterministic across process restarts — langdetect's default behavior
# seeds its internal RNG from the system clock, which makes the same input
# text occasionally classify differently between calls. Not what you want
# when the same detection feeds directly into "which language must the
# model reply in."
_LangDetectFactory.seed = 0

_LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ar": "Arabic",
    "zh-cn": "Chinese", "zh-tw": "Chinese", "ja": "Japanese", "ko": "Korean",
    "ru": "Russian", "tr": "Turkish", "pl": "Polish", "sv": "Swedish",
    "vi": "Vietnamese", "hi": "Hindi", "th": "Thai", "id": "Indonesian",
}


def _detect_reply_language(text: str) -> str | None:
    """
    Figures out, in code, what language the specialist's reply should be
    written in — instead of leaving that judgment to the model via an
    abstract "reply in the user's most recent language" line buried in the
    system prompt (which is what the previous fix relied on).

    That abstract-instruction approach worked for short, single-turn
    factual lookups but broke down in exactly the cases actually seen in
    testing: a conversation that had already mixed several languages
    across turns, combined with a longer multi-step answer (tool call +
    grounding instruction + freshness instruction + language instruction
    all competing for the model's attention on the same generation). The
    model sometimes picked the wrong turn to match, or just picked a
    language on its own — confirmed by English questions coming back in
    Chinese, Japanese, or German. Detecting the language deterministically
    here and stating it explicitly, per turn, removes that judgment call
    from the model entirely rather than asking it to infer it more
    carefully.

    Returns None for text too short to classify reliably (e.g. "hi") —
    callers should leave the system prompt's own language instruction as
    the fallback in that case, not force a possibly-wrong detection.
    """
    text = text.strip()
    if len(text) < 4:
        return None
    try:
        code = _langdetect_detect(text)
    except LangDetectException:
        return None
    return _LANGUAGE_NAMES.get(code, code)


def _with_language_reminder(messages, reply_language: str | None):
    """
    Returns a NEW list with a language reminder appended to the last human
    message's content — not mutated in place, and never written back to
    graph state, so the persisted conversation history stays clean (and so
    later language detection on that history never sees its own reminder
    text). Appending to the end of the actual last user turn, rather than
    to the system prompt, puts it where a model's attention is strongest
    right before it generates — the same reason few-shot examples and
    critical constraints are usually placed near the end of a prompt.
    """
    if not reply_language:
        return messages
    reminder = (
        f"\n\n[reply-language instruction: write your entire answer in "
        f"{reply_language}. Keep JSON field names, schema/property names, "
        f"and errorType codes exactly as written — do not translate those.]"
    )
    msgs = list(messages)
    for i in range(len(msgs) - 1, -1, -1):
        if getattr(msgs[i], "type", None) == "human":
            msgs[i] = msgs[i].model_copy(update={"content": _as_text(msgs[i].content) + reminder})
            break
    return msgs


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    next: str
    iterations: int
    tool_steps: int
    conversation_id: str


def input_guard(state: AgentState):
    """
    Used to hard-block any message that didn't contain one of a fixed list
    of VDA-5050 keywords — which meant "hello" got blocked before the graph
    ever reached the Supervisor, since a plain greeting matches none of
    them. A static keyword list can't tell "hello" (fine, should get a
    friendly reply) apart from "what's your favorite pizza topping" (should
    be politely declined) — that distinction now belongs to the Supervisor's
    SmallTalk vs FINISH routing below, which actually understands the
    message instead of pattern-matching it. This guard now only screens out
    truly empty input.
    """
    last_msg = _as_text(state["messages"][-1].content).strip()
    if not last_msg:
        return {"messages": [AIMessage(content="GUARDRAIL_BLOCK: Empty message.")]}
    return {"messages": []}


def output_guard(state: AgentState):
    last_msg = _as_text(state["messages"][-1].content)
    if "[insert" in last_msg.lower() or "example.com" in last_msg.lower():
        return {"messages": [AIMessage(
            content="GUARDRAIL_BLOCK: The response contained placeholders or hallucinated domains."
        )]}
    return {"messages": []}


def _last_ai_content(messages) -> Optional[str]:
    for m in reversed(messages):
        if getattr(m, "type", None) == "ai" and m.content:
            return _as_text(m.content)
    return None


# Matches VDA 5050 errorType-style tokens (e.g. NODE_UNREACHABLE,
# LOCALIZATION_ERROR, BANANA_ERROR) — two or more ALL_CAPS_WORDS joined by
# underscores. If a question contains one of these, it's almost certainly a
# diagnostics/error-lookup question regardless of what else the sentence
# says, so route there directly instead of relying on the small router
# model's judgment call, which proved unreliable on exactly this pattern
# (NODE_UNREACHABLE and LOCALIZATION_ERROR were both wrongly sent to FINISH
# in testing, while the differently-phrased BANANA_ERROR question wasn't).
_ERROR_TOKEN_PATTERN = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]{2,})+\b")


class Route(BaseModel):
    next: Literal["ProtocolSpecialist", "SchemaSpecialist", "DiagnosticsSpecialist", "SmallTalk", "FINISH"] = Field(
        description=(
            "Select ProtocolSpecialist for rules/protocols, SchemaSpecialist "
            "for JSON structures, DiagnosticsSpecialist for payload validation "
            "or errorType lookups, SmallTalk for pure greetings/thanks/farewells "
            "with no real question, or FINISH for a genuine question about "
            "something else entirely (out of scope)."
        )
    )


SMALLTALK_PROMPT = (
    "You are the friendly front door of a VDA 5050 fleet-operations "
    "assistant. The user's last message was small talk (a greeting, thanks, "
    "farewell, or similar pleasantry) rather than a technical question. "
    "Reply warmly in ONE short sentence, in the same language the user "
    "wrote in, then briefly invite them to ask about VDA 5050 protocol "
    "rules, JSON schemas, payload validation, or error codes. Do not answer "
    "questions about any other topic — you only handle small talk here."
)


def get_smalltalk_responder():
    # Small, cheap conversational replies only — no tools, no structured
    # output. Same model family/fallback pattern as the specialist chain.
    primary = ChatGroq(model="openai/gpt-oss-120b", temperature=0.4)
    fallback = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0.4)
    return primary.with_fallbacks([fallback])


def get_supervisor():
    # llama-3.1-8b-instant is decommissioned by Groq on Aug 16, 2026 —
    # openai/gpt-oss-20b is Groq's own recommended replacement (confirmed
    # via console.groq.com/docs/deprecations), supports structured output,
    # and is on the free tier. Its free-tier daily quota (1K RPD) is much
    # lower than the old model's (14.4K RPD) though, so this now falls back
    # to Gemini on any Groq failure — including a rate limit — instead of
    # the old bare try/except, which silently mislabeled every failure as
    # "out of scope" (see the `except Exception` block below).
    primary = ChatGroq(model="openai/gpt-oss-20b", temperature=0).with_structured_output(Route)
    fallback = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0).with_structured_output(Route)
    router = primary.with_fallbacks([fallback])
    smalltalk_responder = get_smalltalk_responder()

    def supervisor_node(state: AgentState):
        messages = state["messages"]
        iterations = state.get("iterations", 0) + 1

        if iterations > MAX_ITERATIONS:
            partial = _last_ai_content(messages) or (
                "I reached my step limit before fully answering this question. "
                "Here is what I found so far — try rephrasing or narrowing your question."
            )
            return {"next": "FINISH", "iterations": iterations, "messages": [AIMessage(content=partial)]}

        last = messages[-1]
        if getattr(last, "type", None) == "ai" and not getattr(last, "tool_calls", None):
            return {"next": "FINISH", "iterations": iterations}

        last_text = _as_text(last.content)

        # Deterministic override — skip the LLM router entirely for a clear
        # errorType-lookup signal. See _ERROR_TOKEN_PATTERN above for why.
        if getattr(last, "type", None) == "human" and _ERROR_TOKEN_PATTERN.search(last_text):
            print(f"[Supervisor] routed to: DiagnosticsSpecialist (pattern match) | question: {last_text[:60]!r}")
            return {"next": "DiagnosticsSpecialist", "iterations": iterations}

        prompt = SUPERVISOR_PROMPT + "\n\nUser Question: " + last_text

        try:
            decision = router.invoke([HumanMessage(content=prompt)])
            next_node = decision.next
            if next_node not in ("ProtocolSpecialist", "SchemaSpecialist", "DiagnosticsSpecialist", "SmallTalk", "FINISH"):
                next_node = "FINISH"
        except Exception as e:
            # Both Groq (openai/gpt-oss-20b) and the Gemini fallback failed.
            print(f"[Supervisor] Router call failed (Groq + Gemini fallback both failed): {type(e).__name__}: {e}")
            next_node = "FINISH"

        print(f"[Supervisor] routed to: {next_node} | question: {last_text[:60]!r}")

        if next_node == "SmallTalk":
            try:
                reply = smalltalk_responder.invoke([
                    {"role": "system", "content": SMALLTALK_PROMPT},
                    HumanMessage(content=last_text),
                ])
                reply_text = _as_text(reply.content)
            except Exception as e:
                print(f"[Supervisor] Small-talk responder failed: {type(e).__name__}: {e}")
                reply_text = (
                    "Hello! I'm the VDA 5050 fleet assistant — ask me about "
                    "protocol rules, JSON schemas, payload validation, or error codes."
                )
            return {
                "next": "SmallTalk",
                "iterations": iterations,
                "messages": [AIMessage(content=reply_text)],
            }

        if next_node == "FINISH":
            return {
                "next": "FINISH",
                "iterations": iterations,
                "messages": [AIMessage(
                    content="This question appears to be outside the VDA-5050 scope I can help with."
                )],
            }

        return {"next": next_node, "iterations": iterations}
    return supervisor_node


def create_agent_node(tools, system_prompt):
    bound_chain = build_specialist_chain(tools=tools)
    plain_chain = build_specialist_chain(tools=None)

    def agent_node(state: AgentState):
        messages = state["messages"]
        tool_steps = state.get("tool_steps", 0)
        conversation_id = state.get("conversation_id", "")

        last_human_text = ""
        for m in reversed(messages):
            if getattr(m, "type", None) == "human":
                last_human_text = _as_text(m.content)
                break
        reply_language = _detect_reply_language(last_human_text)
        messages_for_llm = _with_language_reminder(messages, reply_language)

        context_note = (
            f"\n\nThe current conversation_id is '{conversation_id}'. ALWAYS pass this "
            "conversation_id on every search call (not just as a retry) — it's what "
            "makes your tools also match: (a) documents uploaded globally to the "
            "knowledge base (visible to every conversation), and (b) documents "
            "uploaded specifically to this conversation. Omitting it only searches "
            "the shipped base VDA 5050 spec and misses both kinds of uploads."
            if conversation_id else ""
        )

        if tool_steps >= MAX_TOOL_STEPS:
            forced_prompt = (
                system_prompt + context_note
                + "\n\nYou have used your available search attempts. Answer now using "
                  "only the information you have already retrieved. Do not request more tools."
            )
            response = plain_chain.invoke([{"role": "system", "content": forced_prompt}] + messages_for_llm)
            response = response.model_copy(update={"content": _as_text(response.content)})
            return {"messages": [response], "tool_steps": tool_steps}

        response = bound_chain.invoke(
            [{"role": "system", "content": system_prompt + context_note}] + messages_for_llm
        )
        new_tool_steps = tool_steps + 1 if response.tool_calls else tool_steps
        # Normalize .content to a string here — the one place every specialist
        # response passes through — so nothing downstream (guards, the
        # frontend) ever has to deal with list-typed content again. tool_calls
        # and everything else on the message are preserved as-is.
        response = response.model_copy(update={"content": _as_text(response.content)})
        return {"messages": [response], "tool_steps": new_tool_steps}

    return agent_node


def build_graph(mcp_tools, checkpointer=None):
    protocol_tools = [t for t in mcp_tools if "protocol" in t.name.lower()]
    schema_tools = [t for t in mcp_tools if "schema" in t.name.lower()]
    if not protocol_tools:
        protocol_tools = mcp_tools
    if not schema_tools:
        schema_tools = mcp_tools
    diagnostics_tools = [ask_diagnostics_agent]

    # Grounding instruction, shared by both retrieval specialists — per
    # docs/PROPOSAL.md Section 2.2: "answer only from retrieved context,
    # and state plainly when the retrieved context does not contain the
    # answer, rather than filling the gap from general knowledge."
    GROUNDING_INSTRUCTION = (
        " Answer only using what your tool calls actually return — do not "
        "fill gaps from general VDA 5050 knowledge you may already have. "
        "If your searches don't contain the answer, say so plainly instead "
        "of guessing."
    )

    # Per task 4: models were replying in the user's language but leaving
    # technical vocabulary in English inconsistently. This makes the rule
    # explicit and gives the model a clear line to hold: translate every
    # word of your OWN prose, never translate a token that comes from the
    # spec/schema itself.
    LANGUAGE_INSTRUCTION = (
        " Always reply in the same language the user's most recent message "
        "was written in — translate your explanations fully into that "
        "language, do not mix in English sentence fragments. The one "
        "exception: JSON field/property names, schema or definition names, "
        "MQTT topic strings, enum values, and errorType codes (e.g. "
        "headerId, order.nodes.nodePosition, NODE_UNREACHABLE) are literal "
        "identifiers from the standard itself — keep those exactly as "
        "written, in their original form, even inside an otherwise "
        "translated sentence."
    )

    # Uploaded documents can be deleted mid-conversation (see the
    # documents endpoints in api/main.py). A model that just re-reads its
    # own earlier tool results from chat history instead of searching again
    # will confidently repeat content that no longer exists. This doesn't
    # eliminate that risk on every model, but it gives the model an explicit
    # instruction to prefer a fresh search over trusting its own memory for
    # exactly the content most likely to have changed underneath it.
    FRESHNESS_INSTRUCTION = (
        " If this conversation already discussed a custom, manufacturer-"
        "specific, or uploaded-document topic earlier, search again rather "
        "than answering from what you remember saying before — uploaded "
        "documents can be deleted mid-conversation, and a stale memory of "
        "them is worse than saying you can no longer find it."
    )

    protocol_agent = create_agent_node(
        protocol_tools,
        "You are the Protocol Specialist. Use your tools to search the VDA-5050 markdown rules to answer."
        + GROUNDING_INSTRUCTION
        + FRESHNESS_INSTRUCTION
        + LANGUAGE_INSTRUCTION
    )
    schema_agent = create_agent_node(
        schema_tools,
        "You are the Schema Specialist. Use your tools to search the VDA-5050 JSON schemas to answer."
        + GROUNDING_INSTRUCTION
        + FRESHNESS_INSTRUCTION
        + LANGUAGE_INSTRUCTION
    )
    diagnostics_agent = create_agent_node(
        diagnostics_tools,
        "You are the Fleet Diagnostics & Validation Specialist. Call ask_diagnostics_agent "
        "ONCE, passing the user's diagnostics question through in natural language (including "
        "any JSON payload they gave you, verbatim). Agent System B's own agent decides whether "
        "it's a payload-validation question or an errorType lookup, runs the correct deterministic "
        "check against the real standard, and returns a plain-language answer — relay that answer "
        "back to the user (translated into their language if needed, but keep any errorType codes, "
        "field names, or JSON keys exactly as returned). Do not call the tool again for the same "
        "question just because the answer says something was 'not found' — that is the final, "
        "correct result, not a reason to retry with different capitalization or phrasing."
        + LANGUAGE_INSTRUCTION
    )

    workflow = StateGraph(AgentState)
    workflow.add_node("InputGuard", input_guard)
    workflow.add_node("Supervisor", get_supervisor())
    workflow.add_node("ProtocolSpecialist", protocol_agent)
    workflow.add_node("SchemaSpecialist", schema_agent)
    workflow.add_node("DiagnosticsSpecialist", diagnostics_agent)
    workflow.add_node("OutputGuard", output_guard)
    workflow.add_node("protocol_tools", ToolNode(protocol_tools))
    workflow.add_node("schema_tools", ToolNode(schema_tools))
    workflow.add_node("diagnostics_tools", ToolNode(diagnostics_tools))

    workflow.add_edge(START, "InputGuard")

    workflow.add_conditional_edges(
        "InputGuard",
        lambda x: "Supervisor" if not x["messages"][-1].content.startswith("GUARDRAIL_BLOCK") else END,
        {"Supervisor": "Supervisor", END: END},
    )

    workflow.add_conditional_edges(
        "Supervisor",
        lambda x: x["next"],
        {
            "ProtocolSpecialist": "ProtocolSpecialist",
            "SchemaSpecialist": "SchemaSpecialist",
            "DiagnosticsSpecialist": "DiagnosticsSpecialist",
            "SmallTalk": "OutputGuard",
            "FINISH": "OutputGuard",
        },
    )

    workflow.add_conditional_edges(
        "ProtocolSpecialist",
        lambda x: "protocol_tools" if x["messages"][-1].tool_calls else "Supervisor",
        {"protocol_tools": "protocol_tools", "Supervisor": "Supervisor"},
    )
    workflow.add_edge("protocol_tools", "ProtocolSpecialist")

    workflow.add_conditional_edges(
        "SchemaSpecialist",
        lambda x: "schema_tools" if x["messages"][-1].tool_calls else "Supervisor",
        {"schema_tools": "schema_tools", "Supervisor": "Supervisor"},
    )
    workflow.add_edge("schema_tools", "SchemaSpecialist")

    workflow.add_conditional_edges(
        "DiagnosticsSpecialist",
        lambda x: "diagnostics_tools" if x["messages"][-1].tool_calls else "Supervisor",
        {"diagnostics_tools": "diagnostics_tools", "Supervisor": "Supervisor"},
    )
    workflow.add_edge("diagnostics_tools", "DiagnosticsSpecialist")

    workflow.add_edge("OutputGuard", END)

    return workflow.compile(checkpointer=checkpointer)