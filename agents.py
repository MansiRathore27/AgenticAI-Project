import json
import os
import re
from typing import TypedDict

from dotenv import load_dotenv
from groq import Groq
from langgraph.graph import StateGraph, START, END

from tools import search_government_schemes

load_dotenv()
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
MAX_RETRIES = 2
MAX_RESULTS = 18


class AgentState(TypedDict, total=False):
    user_query: str
    conversation_history: list
    user_profile: dict
    intent: str
    search_queries: list
    search_results: list
    plan: list
    retries: int
    retry_requested: bool
    retry_reason: str
    trace: list
    final_answer: str
    errors: list
    evidence_score: int


def get_client():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY is missing. Add it to your .env file.")
    return Groq(api_key=key)


def ask_llm(system_prompt: str, user_prompt: str, json_mode: bool = False) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_completion_tokens": 2200,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = get_client().chat.completions.create(**payload)
    return response.choices[0].message.content or ""


def parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The AI did not return a valid JSON object.")
    return json.loads(raw[start:end + 1])


def _trace(state, message):
    return list(state.get("trace", [])) + [message]


def _merge_results(old_results, new_results, limit=MAX_RESULTS):
    """Keep earlier evidence when a retry adds new sources."""
    merged = {}
    for item in list(old_results or []) + list(new_results or []):
        url = (item.get("url") or "").strip()
        if not url:
            continue
        key = url.rstrip("/").lower()
        if key not in merged:
            merged[key] = dict(item)
        else:
            old = merged[key]
            # Keep the richer page text and strongest metadata.
            for field in ("page_content", "page_title", "snippet", "title"):
                if len(str(item.get(field) or "")) > len(str(old.get(field) or "")):
                    old[field] = item.get(field)
            for field in ("page_status", "is_scheme_page", "looks_scheme_related", "is_generic_portal"):
                if item.get(field) and not old.get(field):
                    old[field] = item[field]
    values = list(merged.values())
    values.sort(key=lambda x: (
        not bool((x.get("page_content") or "").strip()),
        not bool(x.get("is_scheme_page") or x.get("looks_scheme_related")),
        -len(x.get("page_content") or ""),
    ))
    return values[:limit]


def planner_agent(state: AgentState) -> dict:
    query = (state.get("user_query") or "").strip()
    history = state.get("conversation_history", [])[-8:]
    profile = dict(state.get("user_profile", {}))
    prompt = f"""
Current user message:
{query}

Recent conversation:
{json.dumps(history, ensure_ascii=False)}

Known profile (only explicit facts; do not infer sensitive or missing values):
{json.dumps(profile, ensure_ascii=False)}

Return one JSON object:
{{
 "intent":"find_schemes|check_eligibility|application_help|general_question",
 "profile_updates":{{"age":null,"state":null,"education":null,"course":null,
 "annual_income":null,"category":null,"gender":null,"disability":null,
 "occupation":null,"residence_type":null,"domicile":null}},
 "search_queries":["query one","query two","query three"],
 "plan":["action one","action two","action three"]
}}
Rules:
- Extract facts only when explicitly stated in the message/history. Do not infer category/caste.
- For scholarship/benefit discovery, include central-government, state-government and course-related routes.
- Use concise search queries focused on official Indian government sources.
- Do not invent scheme names or eligibility rules.
"""
    try:
        data = parse_json_response(ask_llm(
            "You are a planning agent. Make an executable plan and extract only stated facts; do not answer the user's question.",
            prompt, json_mode=True
        ))
        updates = data.get("profile_updates") or {}
        merged = dict(profile)
        for key, value in updates.items():
            if value is not None and str(value).strip():
                merged[key] = value
        queries = [str(q).strip() for q in (data.get("search_queries") or []) if str(q).strip()][:4]
        if not queries:
            queries = _fallback_queries(query, merged)
        return {
            "intent": data.get("intent", "find_schemes"),
            "user_profile": merged,
            "search_queries": queries,
            "plan": data.get("plan") or ["Search official sources", "Evaluate retrieved evidence", "Check profile fit and explain next steps"],
            "retry_requested": False,
            "trace": _trace(state, "Planner: extracted explicit profile facts and prepared targeted search routes."),
            "errors": list(state.get("errors", [])),
        }
    except Exception as exc:
        return {
            "intent": "find_schemes",
            "user_profile": profile,
            "search_queries": _fallback_queries(query, profile),
            "plan": ["Search official government sources", "Review page evidence", "Explain limits and next steps"],
            "retry_requested": False,
            "trace": _trace(state, "Planner fallback: used rule-based queries because planning output was unavailable."),
            "errors": list(state.get("errors", [])) + [f"Planner note: {str(exc)[:250]}"],
        }


def _fallback_queries(query, profile):
    state_name = profile.get("state") or ""
    course = profile.get("course") or profile.get("education") or "student"
    return [
        f"{query} official government scheme eligibility India",
        f"{state_name} student scholarship scheme eligibility official government",
        f"{course} scholarship financial assistance site:gov.in OR site:nic.in",
    ]


def search_agent(state: AgentState) -> dict:
    """Execute planner-selected tools and merge new evidence with previous attempts."""
    queries = state.get("search_queries", [])[:4]
    new_results, errors = [], list(state.get("errors", []))
    for query in queries:
        try:
            result = search_government_schemes(query, max_results=6, fetch_pages=True)
            errors.extend(result.get("errors", []))
            for item in result.get("results", []):
                row = dict(item)
                row["search_query_used"] = query
                new_results.append(row)
        except Exception as exc:
            errors.append(f"Search tool error for '{query[:80]}': {str(exc)[:220]}")
    combined = _merge_results(state.get("search_results", []), new_results)
    return {
        "search_results": combined,
        "errors": errors[-20:],
        "retry_requested": False,
        "trace": _trace(state, f"Tool executor: ran {len(queries)} query route(s); retained {len(combined)} unique official-domain results across all attempts."),
    }


def _quality(results):
    """Score retrieved evidence quality, not how many search results were found."""
    fetched = [
        r for r in results
        if r.get("page_status") == "FETCHED"
        and len((r.get("page_content") or "").strip()) >= 350
        and not r.get("is_generic_portal")
    ]
    criteria_terms = (
        "eligibility", "eligible", "who can apply", "annual family income",
        "family income", "domicile", "resident of", "minimum marks",
        "percentage", "age limit", "course of study", "recognized institution",
        "category", "applicant must", "students must", "income limit"
    )
    detail_pages = []
    for r in fetched:
        # Do not use search snippets or titles to claim eligibility evidence.
        body = (r.get("page_content") or "").lower()
        if (r.get("is_scheme_page") or r.get("looks_scheme_related")) and any(
            term in body for term in criteria_terms
        ):
            detail_pages.append(r)

    # Results alone earn very little. Only fetched text with actual criteria earns points.
    score = min(20, len(fetched) * 4) + min(80, len(detail_pages) * 20)
    score = min(100, score)
    return score, fetched, detail_pages


def reflect_agent(state: AgentState) -> dict:
    results = state.get("search_results", [])
    retries = int(state.get("retries", 0))
    score, fetched, detail_pages = _quality(results)
    should_retry = score < 35 and retries < MAX_RETRIES
    if should_retry:
        profile = state.get("user_profile", {})
        state_name = profile.get("state") or ""
        course = profile.get("course") or profile.get("education") or "student"
        retry_queries = [
            f"{state_name} official scholarship eligibility student government portal",
            f"{course} student scholarship eligibility benefits site:gov.in OR site:nic.in",
            "site:myscheme.gov.in/schemes student education scholarship eligibility",
        ]
        prior = set(state.get("search_queries", []))
        retry_queries = [q for q in retry_queries if q not in prior]
        if not retry_queries:
            retry_queries = [f"{state_name} student financial assistance official scheme", f"{course} scholarship official government India"]
        return {
            "search_queries": retry_queries[:3],
            "retries": retries + 1,
            "retry_requested": True,
            "retry_reason": f"Evidence score {score}/100; {len(detail_pages)} fetched page(s) had criteria-related text.",
            "evidence_score": score,
            "trace": _trace(state, f"Reflection: evidence is weak ({score}/100); changing search strategy and retrying ({retries + 1}/{MAX_RETRIES})."),
        }
    return {
        "retries": retries,
        "retry_requested": False,
        "retry_reason": state.get("retry_reason", ""),
        "evidence_score": score,
        "trace": _trace(state, f"Reflection: evidence score {score}/100; {len(fetched)} useful page(s) fetched and {len(detail_pages)} page(s) contain criteria-related evidence. Proceeding with appropriate uncertainty."),
    }


def should_retry(state: AgentState) -> str:
    return "search" if state.get("retry_requested") else "respond"


def _evidence_summary(results):
    items = []
    for r in results[:12]:
        content = (r.get("page_content") or "").strip()
        items.append({
            "title": r.get("page_title") or r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("snippet") or "")[:500],
            "page_status": r.get("page_status", "NOT_FETCHED"),
            "page_content": content[:1800],
            "evidence_type": "retrieved_page_text" if content else "search_snippet_only",
            "is_scheme_page": bool(r.get("is_scheme_page")),
            "looks_scheme_related": bool(r.get("looks_scheme_related")),
            "is_generic_portal": bool(r.get("is_generic_portal")),
        })
    return items


def response_agent(state: AgentState) -> dict:
    query = state.get("user_query", "")
    profile = state.get("user_profile", {})
    results = state.get("search_results", [])
    score = int(state.get("evidence_score", 0))
    evidence = _evidence_summary(results)
    prompt = f"""
User request: {query}
User profile (explicitly supplied only): {json.dumps(profile, ensure_ascii=False)}
Evidence score: {score}/100
Retrieved evidence:
{json.dumps(evidence, ensure_ascii=False)}

Write a clear answer in simple English. Follow these rules strictly:
- Treat the supplied evidence as the ONLY source of scheme-specific facts.
- Do not use your general knowledge to fill missing scheme details.
- Never claim a named scheme is suitable for M.Tech, a particular age, state, income, category or course unless that exact fact is supported by retrieved page text or clearly marked search snippet.
- Search snippets are leads only, not verified rules. Say "search snippet only" when relevant.
- Do not invent benefits, amounts, income limits, deadlines, documents, application steps, or eligibility.
- Do not tell the user to create OTR, apply, or gather specific documents unless the retrieved evidence explicitly supports that step.
- A high evidence score is not proof that this particular user is eligible.
- Distinguish "not found in retrieved evidence" from "the user is ineligible".
- Generic portals are not schemes. List them only as official places to check further.
- Give a short table or bullets with: scheme/page name, what evidence supports, what remains unknown, and source URL.
- End with practical next steps and at most 3 questions for missing profile details.
- Never say an application was submitted.
"""
    try:
        answer = ask_llm(
            "You are a cautious evidence-grounded government benefits assistant. Unsupported claims are forbidden. If evidence is insufficient, clearly say so rather than guessing.",
            prompt
        )
        # Deterministic guardrail: if no page with criteria evidence exists, explicitly disclose it.
        _, _, detail_pages = _quality(results)
        if not detail_pages:
            disclaimer = (
                "\n\n**Evidence limitation:** I could not retrieve an official scheme-detail page "
                "containing usable eligibility criteria. Therefore, I cannot confirm that any named "
                "scheme matches your profile. Search-result snippets and portal homepages are not "
                "enough to verify eligibility."
            )
            if "Evidence limitation:" not in answer:
                answer += disclaimer
    except Exception as exc:
        answer = (
            "I could not generate the AI summary because the language-model request failed. "
            "The search results are not enough to confirm eligibility. Please check the official "
            "source links below; no application was submitted."
        )
        return {
            "final_answer": answer,
            "errors": list(state.get("errors", [])) + [f"Response agent error: {str(exc)[:250]}"],
            "trace": _trace(state, "Response agent used a safe fallback after an LLM error."),
        }
    return {
        "final_answer": answer,
        "trace": _trace(state, "Response agent: summarized only retrieved evidence and explicitly disclosed unsupported eligibility details."),
    }


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_agent)
    graph.add_node("search", search_agent)
    graph.add_node("reflect", reflect_agent)
    graph.add_node("respond", response_agent)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "search")
    graph.add_edge("search", "reflect")
    graph.add_conditional_edges("reflect", should_retry, {"search": "search", "respond": "respond"})
    graph.add_edge("respond", END)
    return graph.compile()


def run_agent(user_query: str, user_profile: dict | None = None,
              conversation_history: list | None = None) -> dict:
    initial: AgentState = {
        "user_query": user_query,
        "user_profile": user_profile or {},
        "conversation_history": conversation_history or [],
        "intent": "find_schemes",
        "search_queries": [],
        "search_results": [],
        "plan": [],
        "retries": 0,
        "retry_requested": False,
        "retry_reason": "",
        "trace": [],
        "final_answer": "",
        "errors": [],
        "evidence_score": 0,
    }
    return build_agent_graph().invoke(initial)


if __name__ == "__main__":
    print("Agentic Government Benefits Assistant")
    print("Type a request, or type 'exit' to stop.")
    profile, history = {}, []
    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue
        try:
            result = run_agent(question, profile, history)
            profile = result.get("user_profile", profile)
            print("\nAssistant:\n", result.get("final_answer", "No response generated."))
            print("\nAgent trace:")
            for line in result.get("trace", []):
                print("-", line)
            if result.get("errors"):
                print("\nWarnings:")
                for error in result["errors"][-8:]:
                    print("-", error)
            history.extend([
                {"role": "user", "content": question},
                {"role": "assistant", "content": result.get("final_answer", "")},
            ])
        except Exception as exc:
            print(f"\nRun failed: {exc}")
