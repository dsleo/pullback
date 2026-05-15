"""Query planning and replanning service for the librarian orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from ..observability import get_agent_instrumentation, get_logger

log = get_logger("librarian.query_planner")


class QueryPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(
        default_factory=list,
        description="Ordered list of concise search queries.",
    )


@dataclass(frozen=True)
class QueryPlannerDeps:
    original_query: str
    max_query_attempts: int


class QueryPlannerService:
    def __init__(
        self,
        *,
        model_name: str,
        enabled: bool,
        max_query_attempts: int,
        timeout_seconds: float,
        usage_limits: UsageLimits,
    ) -> None:
        self._enabled = enabled
        self._model_name = model_name
        self._max_query_attempts = max(1, max_query_attempts)
        self._timeout_seconds = timeout_seconds
        self._usage_limits = usage_limits
        self._expansion_cache: dict[str, list[str]] = {}  # Cache query expansions

        self.agent = Agent(
            model_name,
            deps_type=QueryPlannerDeps,
            output_type=QueryPlanOutput,
            instrument=get_agent_instrumentation(),
            instructions=(
                "You are a mathematical literature search expert. Your task is to rewrite a single "
                "math query into a small set of MAXIMALLY DIVERSE search queries that together maximize "
                "recall across different paper databases (arXiv, zbMATH, OpenAlex).\n"
                "\n"
                "CRITICAL: Queries that are too similar return duplicate results. Each query MUST "
                "target a meaningfully different angle. Trivial paraphrases waste slots.\n"
                "\n"
                "Output JSON with exactly one key: queries (list of strings).\n"
                "Hard constraints:\n"
                "- Return at most the requested number of queries.\n"
                "- Each query <= 14 words.\n"
                "- No invented author names, venues, or arXiv IDs.\n"
                "\n"
                "USE EXACTLY THESE STRATEGIES, one per slot, in order:\n"
                "\n"
                "1. NOUN-PHRASE (paper-title style): compress to key mathematical objects only, "
                "drop all logical connectives and quantifiers. Matches paper titles.\n"
                "   Example: 'Every compact metric space embeds isometrically into a Banach space' "
                "-> 'isometric embedding compact metric space Banach space'\n"
                "\n"
                "2. SYNONYM-SWAP: replace 2-3 key terms with standard mathematical synonyms or "
                "equivalent formulations from different subfields. Change vocabulary substantially.\n"
                "   Example: 'smooth DM stack codimension one' "
                "-> 'smooth Deligne-Mumford algebraic stack divisor determines'\n"
                "\n"
                "3. ABSTRACTION-OR-SPECIALIZATION: state the result in a broader foundational form, "
                "or identify the specific instance. Use different nouns entirely.\n"
                "   Example: 'quotient of root stack of DVR' "
                "-> 'root stack local ring tame ramification quotient'\n"
                "\n"
                "4. ENTITY-ATTRIBUTION (if slot available): If you can RELIABLY identify the author(s) "
                "of this result with HIGH confidence, generate a query with their last name(s) + "
                "3-4 key mathematical terms from the statement. "
                "CRITICAL: Only use this slot if you are certain about the attribution — a wrong "
                "author name makes the query useless. Do NOT invent plausible-sounding names. "
                "SKIP this slot entirely if attribution is uncertain by using a different strategy instead.\n"
                "   Example: 'Quasi-coherent sheaves satisfy fpqc descent' "
                "-> 'Vistoli quasi-coherent sheaves fpqc descent'\n"
                "   Example: 'Compact metric space embeds isometrically into Banach space' "
                "-> 'Grothendieck isometric embedding compact metric space'\n"
                "   Example: 'Gluing and clutching morphisms for twisted stable maps' "
                "-> 'Abramovich Vistoli twisted stable maps gluing'\n"
                "\n"
                "5. KEYWORD-FIELD (if slot available): 2-4 bare mathematical keywords, no sentence "
                "structure. Include the subfield name.\n"
                "   Example: 'algebraic stacks moduli codimension boundary'\n"
                "\n"
                "6. SUBJECT-AREA (if slot available): describe the PAPER's overall domain and type, "
                "NOT the specific theorem. Ask yourself: what kind of paper would prove this? Use "
                "topic-level vocabulary that would appear in a paper's title or abstract — not the "
                "theorem statement. This slot is especially important for queries describing internal "
                "lemmas or propositions.\n"
                "   Example: 'Monotonicity of frequency function for elliptic operators on Dini domains' "
                "-> 'unique continuation elliptic equations boundary regularity harmonic analysis'\n"
                "   Example: 'Whitney decomposition of a Lipschitz domain' "
                "-> 'harmonic analysis Lipschitz domain boundary behavior elliptic PDE'\n"
                "   Example: 'Any good moduli space map has a section after an alteration' "
                "-> 'good moduli spaces algebraic stacks proper morphisms'\n"
                "\n"
                "Each query must be LEXICALLY DISTINCT from the others — minimal word overlap "
                "beyond unavoidable core mathematical terms.\n"
                "Output JSON only."
            ),
        )

    @staticmethod
    def query_key(query: str) -> str:
        return " ".join(query.lower().split())

    def _simple_attempts(self, base_query: str) -> list[str]:
        if self._max_query_attempts <= 1:
            return [base_query]
        normalized = " ".join(base_query.split())
        hyphen_flat = normalized.replace("-", " ")
        return self.sanitize_attempt_queries(base_query, [hyphen_flat])

    def sanitize_attempt_queries(self, base_query: str, planned_queries: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for q in [base_query] + planned_queries:
            key = self.query_key(q)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(q.strip())
            if len(ordered) >= self._max_query_attempts:
                break
        return ordered or [base_query]

    @staticmethod
    def _normalize_query(text: str) -> str:
        return " ".join(text.split())

    async def _run_plan(self, prompt: str, *, original_query: str, max_query_attempts: int) -> QueryPlanOutput:
        if self._timeout_seconds > 0:
            result = await asyncio.wait_for(
                self.agent.run(
                    prompt,
                    deps=QueryPlannerDeps(
                        original_query=original_query,
                        max_query_attempts=max_query_attempts,
                    ),
                    usage_limits=self._usage_limits,
                ),
                timeout=self._timeout_seconds,
            )
        else:
            result = await self.agent.run(
                prompt,
                deps=QueryPlannerDeps(
                    original_query=original_query,
                    max_query_attempts=max_query_attempts,
                ),
                usage_limits=self._usage_limits,
            )
        return result.output

    async def query_attempts(self, query: str) -> list[str]:
        base = query.strip()
        if not base:
            return []
        if not self._enabled:
            return self._simple_attempts(base)

        # Check cache first
        cache_key = self.query_key(base)
        if cache_key in self._expansion_cache:
            log.info("query_plan.cache_hit query_key={}", cache_key)
            return self._expansion_cache[cache_key]

        log.info(
            "query_plan.start model={} timeout_s={:.2f} max_query_attempts={}",
            self._model_name,
            self._timeout_seconds,
            self._max_query_attempts,
        )
        # Be explicit: include the original query as slot 0, then generate (N-1)
        # distinct variants. Some models interpret "up to N" as "1 is fine".
        variant_count = max(0, self._max_query_attempts - 1)
        prompt = (
            f"Original query: {base}\n"
            f"Return a JSON object with a 'queries' list of length {1 + variant_count}.\n"
            "- queries[0] MUST be exactly the original query.\n"
            f"- Generate exactly {variant_count} additional queries in queries[1:]\n"
            "  (do not repeat the original, and do not leave blanks).\n"
            "Each additional query must use a DIFFERENT strategy and be lexically distinct from the others.\n"
            "Do NOT produce minor rewrites — each query should look like it was written by someone "
            "approaching the problem from a completely different angle.\n"
            "If you are unsure about author/entity attribution, use a different safe strategy instead "
            "(do not reduce the number of queries).\n"
            "Respond only with the JSON object containing queries."
        )
        try:
            output = await self._run_plan(
                prompt,
                original_query=base,
                max_query_attempts=self._max_query_attempts,
            )
            planned = [q.strip() for q in output.queries if q and q.strip()]
            attempts = self.sanitize_attempt_queries(base, planned)
            log.info("query_plan.generated attempts={}", attempts)
            # Cache the result
            self._expansion_cache[cache_key] = attempts
            return attempts or [base]
        except Exception as exc:
            log.warning(
                "query_plan.failed model={} timeout_s={:.2f} error_type={} error_repr={} fallback=base_query_only",
                self._model_name,
                self._timeout_seconds,
                type(exc).__name__,
                repr(exc),
            )
            result = self._simple_attempts(base)
            # Cache fallback result too
            self._expansion_cache[cache_key] = result
            return result

    async def next_replan_seed(self, *, original_query: str, seen_queries: list[str]) -> str | None:
        seen_keys = {self.query_key(item) for item in seen_queries}
        seen_block = "\n".join(f"- {item}" for item in seen_queries[-8:])
        prompt = (
            "A mathematician is searching for a known statement/result in the literature, "
            "possibly phrased differently.\n"
            f"The orriginal query was: {original_query}\n"
            "You are an expert in mathematical literature search. "
            "You previously proposed the queries below, but they did not return satisfactory matches.\n"
            "Already tried queries:\n"
            f"{seen_block or '- (none)'}\n"
            "Return one new query that targets the same statement/result using different terminology.\n"
            "It must be meaningfully different from the tried queries (not a light rephrase).\n"
            "You may broaden or narrow focus, but keep the same mathematical intent.\n"
            "Respond only with the JSON object containing 'queries'."
        )
        try:
            output = await self._run_plan(
                prompt,
                original_query=original_query,
                max_query_attempts=1,
            )
            for candidate in [q.strip() for q in output.queries if q and q.strip()]:
                if self.query_key(candidate) not in seen_keys:
                    return candidate
        except Exception as exc:
            log.warning(
                "query_replan.failed model={} timeout_s={:.2f} error_type={} error_repr={}",
                self._model_name,
                self._timeout_seconds,
                type(exc).__name__,
                repr(exc),
            )
        return None
