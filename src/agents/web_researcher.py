"""
Web Researcher Agent (StackOverflow Fallback) for Amaze on Work.

Triggered when the validation agent has exhausted 3 LLM fix attempts
on the same error signature with no improvement.

Searches:
  1. StackOverflow API — top accepted answers for the error
  2. DuckDuckGo Instant Answer API — broader context
  3. LLM summarizes top findings into actionable fix_hint + code_example

The result is appended to fix_writer's context as "research_hints"
so the next fix attempt is grounded in real-world solutions.
"""

import json
import logging
import re
from typing import Optional

import httpx

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import PipelineState, AgentType
from src.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)

# StackOverflow API config
SO_API_BASE = "https://api.stackexchange.com/2.3"
SO_SITE = "stackoverflow"
SO_REQUEST_TIMEOUT = 10
SO_MAX_RESULTS = 5


class WebResearcherAgent(BaseAgent):
    """
    Fallback agent that searches StackOverflow and the web for real-world fixes.

    Triggered by the supervisor after 3 failed validation attempts with the
    same error signature. Returns:
        {
            "fix_hint": "Actionable guidance for the fix_writer",
            "sources": [{"url": ..., "title": ..., "score": ...}],
            "code_example": "Most relevant code snippet found"
        }
    """
    agent_type = AgentType.WEB_RESEARCHER

    def execute(self, state: PipelineState) -> AgentResponse:
        incident = state.get("incident", {}) or {}
        root_cause = state.get("root_cause", {}) or {}
        validation_logs = state.get("validation_logs", []) or []

        # Extract error from most recent validation log
        last_log = validation_logs[-1] if validation_logs else {}
        error_sig = last_log.get("error_signature", "") or state.get("last_error_signature", "")

        # Build a clean error query
        error_query = self._build_query(incident, root_cause, error_sig)
        logger.info(f"[WebResearcher] Searching for: {error_query!r}")

        # 1. StackOverflow
        so_results = self._search_stackoverflow(error_query)

        # 2. DuckDuckGo fallback if SO gives nothing
        ddg_results = []
        if not so_results:
            ddg_results = self._search_duckduckgo(error_query)

        all_results = so_results + ddg_results

        # 3. LLM synthesis
        fix_hint, code_example = self._synthesize(error_query, all_results, root_cause)

        data = {
            "fix_hint": fix_hint,
            "code_example": code_example,
            "sources": all_results[:5],
            "query_used": error_query,
        }

        logger.info(f"[WebResearcher] Found {len(all_results)} sources — hint: {fix_hint[:100]!r}")

        return AgentResponse(
            success=True,
            message=f"Research complete — {len(all_results)} sources found. Passing hints to fix_writer.",
            data=data,
            next_agent=AgentType.FIX_WRITER.value,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _build_query(self, incident: dict, root_cause: dict, error_sig: str) -> str:
        """Build a targeted StackOverflow search query from the incident context."""
        title = incident.get("title", "")
        failure_type = incident.get("failure_type", "")
        hypothesis = root_cause.get("hypothesis", "") or ""
        stack_trace = incident.get("error_log", "") or ""

        # Extract exception type from error sig or stack trace
        exception_match = re.search(
            r"([A-Za-z]+(?:Error|Exception|Warning|Fault))[\s:]", stack_trace
        )
        exception_type = exception_match.group(1) if exception_match else ""

        # Prefer: ExceptionType + key phrase from hypothesis
        if exception_type and hypothesis:
            # Extract a key phrase (first non-trivial noun phrase)
            words = re.findall(r"\b[a-z]{4,}\b", hypothesis.lower())
            key_words = [w for w in words if w not in
                         {"that", "with", "from", "this", "when", "because", "which", "have"}]
            phrase = " ".join(key_words[:4])
            return f"{exception_type} {phrase}"[:120]

        if exception_type:
            return f"{exception_type} {title[:60]}"[:120]

        if title:
            return title[:120]

        return f"python {failure_type} bug fix" if failure_type else "production bug fix python"

    def _search_stackoverflow(self, query: str) -> list[dict]:
        """Search StackOverflow API for relevant questions and answers."""
        results = []
        try:
            params = {
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": SO_SITE,
                "filter": "!9_bDDxJY5",  # includes body of answers
                "pagesize": SO_MAX_RESULTS,
                "accepted": "True",  # accepted answers only
            }
            resp = httpx.get(
                f"{SO_API_BASE}/search/advanced",
                params=params,
                timeout=SO_REQUEST_TIMEOUT,
                headers={"User-Agent": "Amaze on Work-AI/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    if not item.get("is_answered"):
                        continue
                    answer_body = ""
                    if item.get("answers"):
                        # Prefer accepted answer
                        answers = sorted(
                            item["answers"],
                            key=lambda a: (a.get("is_accepted", False), a.get("score", 0)),
                            reverse=True,
                        )
                        answer_body = answers[0].get("body_markdown", "")[:1000]

                    results.append({
                        "source": "stackoverflow",
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "score": item.get("score", 0),
                        "answer_votes": item.get("answer_count", 0),
                        "excerpt": self._strip_html(answer_body)[:600],
                    })
            else:
                logger.warning(f"[WebResearcher] SO API returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"[WebResearcher] StackOverflow search failed: {e}")

        # Fallback: try without 'accepted' filter if no results
        if not results:
            try:
                params.pop("accepted", None)
                resp = httpx.get(
                    f"{SO_API_BASE}/search/advanced",
                    params=params,
                    timeout=SO_REQUEST_TIMEOUT,
                    headers={"User-Agent": "Amaze on Work-AI/1.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("items", [])[:3]:
                        results.append({
                            "source": "stackoverflow",
                            "title": item.get("title", ""),
                            "url": item.get("link", ""),
                            "score": item.get("score", 0),
                            "excerpt": self._strip_html(item.get("body", ""))[:400],
                        })
            except Exception:
                pass

        logger.info(f"[WebResearcher] StackOverflow: {len(results)} result(s)")
        return results

    def _search_duckduckgo(self, query: str) -> list[dict]:
        """DuckDuckGo Instant Answer API fallback."""
        results = []
        try:
            resp = httpx.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_redirect": "1"},
                timeout=SO_REQUEST_TIMEOUT,
                headers={"User-Agent": "Amaze on Work-AI/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                abstract = data.get("AbstractText", "")
                abstract_url = data.get("AbstractURL", "")
                if abstract:
                    results.append({
                        "source": "duckduckgo",
                        "title": data.get("Heading", query),
                        "url": abstract_url,
                        "score": 0,
                        "excerpt": abstract[:600],
                    })
                for topic in data.get("RelatedTopics", [])[:3]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append({
                            "source": "duckduckgo",
                            "title": topic.get("Text", "")[:80],
                            "url": topic.get("FirstURL", ""),
                            "score": 0,
                            "excerpt": topic.get("Text", "")[:400],
                        })
        except Exception as e:
            logger.warning(f"[WebResearcher] DuckDuckGo search failed: {e}")

        logger.info(f"[WebResearcher] DuckDuckGo: {len(results)} result(s)")
        return results

    def _synthesize(
        self, query: str, results: list[dict], root_cause: dict
    ) -> tuple[str, str]:
        """LLM synthesizes the top search results into an actionable fix hint."""
        if not results:
            return (
                "No web results found. Consider checking the error class documentation "
                "and reviewing similar past incidents.",
                "",
            )

        # Build results context for LLM
        snippets = []
        for r in results[:4]:
            snippets.append(
                f"Source: {r['source']} | {r.get('title', '')}\n"
                f"URL: {r.get('url', '')}\n"
                f"Excerpt: {r.get('excerpt', '')[:400]}"
            )
        snippets_text = "\n\n---\n\n".join(snippets)

        hypothesis = root_cause.get("hypothesis", "") or ""

        prompt = f"""You are a Staff Engineer reviewing web research to help fix a production bug.

ROOT CAUSE: {hypothesis[:500]}

SEARCH QUERY: {query}

WEB RESEARCH RESULTS:
{snippets_text}

Based on these results, provide:
1. A concise, actionable FIX HINT (2-4 sentences) telling the patch writer EXACTLY what code pattern to use
2. A CODE EXAMPLE (the most relevant code snippet from the results, properly formatted)

Return JSON only:
{{
  "fix_hint": "Specific, actionable guidance for how to fix this exact bug...",
  "code_example": "# Most relevant code snippet\n..."
}}"""

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a Staff Engineer synthesizing web research into actionable fix guidance. "
                        "Be specific, cite exact patterns, return only JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            response = self.llm.complete(messages)
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return data.get("fix_hint", ""), data.get("code_example", "")
        except Exception as e:
            logger.error(f"[WebResearcher] LLM synthesis failed: {e}")

        # Fallback: return best excerpt directly
        best = max(results, key=lambda r: r.get("score", 0))
        return f"See: {best.get('title', '')} — {best.get('url', '')}", best.get("excerpt", "")

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags from StackOverflow answer bodies."""
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
        text = re.sub(r"<pre>(.*?)</pre>", r"\n\1\n", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def get_system_prompt(self) -> str:
        return (
            "You are the Web Researcher Agent in Amaze on Work. "
            "When the LLM fix attempts are exhausted, you search StackOverflow and the web "
            "for real-world solutions and synthesize actionable fix hints for the patch writer."
        )
