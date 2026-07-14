"""
Résumé ↔ recipient relevance matching — invisible, deterministic.

Small models given a whole résumé reliably build every email around the same
flagship project. This pre-pass ranks the résumé's individual facts (projects,
wins, skills) against what THIS recipient cares about — their job-posting
context, designation, and company — and hands the generator an explicit
"build around these" shortlist. An AI company sees the candidate's LLM work
first; a payments company sees the billing system; a DevOps lead sees the
Kubernetes migration. Same résumé, different email.

No signal → no shortlist → the generator behaves exactly as before.
"""

import re
from hashlib import md5

# Tech taxonomy: family → keywords found in résumés and job contexts.
# Word-boundary matched, lowercase. Families let "PyTorch" on the résumé match
# "machine learning" in the job post even with zero shared literal keywords.
TECH_FAMILIES: dict[str, tuple[str, ...]] = {
    "ai_ml": (
        "ai", "ml", "machine learning", "deep learning", "llm", "llms", "gpt",
        "rag", "nlp", "computer vision", "pytorch", "tensorflow", "langchain",
        "embeddings", "fine-tuning", "fine-tuned", "transformer", "inference",
        "openai", "anthropic", "hugging face", "generative", "agents", "chatbot",
        "recommendation", "model serving", "mlops",
    ),
    "backend": (
        "backend", "back-end", "api", "apis", "microservice", "microservices",
        "golang", "go", "python", "java", "node", "nodejs", "rust", "grpc",
        "rest", "graphql", "postgres", "postgresql", "mysql", "redis", "kafka",
        "rabbitmq", "queue", "latency", "throughput", "distributed",
    ),
    "frontend": (
        "frontend", "front-end", "react", "vue", "angular", "svelte", "nextjs",
        "next.js", "typescript", "javascript", "css", "tailwind", "ui", "ux",
        "design system", "accessibility", "webapp",
    ),
    "data": (
        "data engineer", "data engineering", "spark", "airflow", "etl", "dbt",
        "warehouse", "snowflake", "bigquery", "analytics", "data pipeline",
        "pipelines", "streaming", "batch", "sql",
    ),
    "devops_infra": (
        "devops", "sre", "kubernetes", "k8s", "docker", "terraform", "aws",
        "gcp", "azure", "ci/cd", "cicd", "observability", "monitoring",
        "infrastructure", "platform engineering", "reliability", "on-call",
        "serverless", "cloud",
    ),
    "mobile": (
        "android", "ios", "swift", "kotlin", "flutter", "react native",
        "mobile app", "app store", "play store",
    ),
    "fintech": (
        "payments", "payment", "stripe", "billing", "fintech", "banking",
        "ledger", "transactions", "kyc", "fraud", "trading", "crypto",
    ),
    "security": (
        "security", "auth", "authentication", "oauth", "encryption", "sso",
        "compliance", "penetration", "vulnerability", "appsec",
    ),
    "scraping_search": (
        "scraping", "crawler", "search", "elasticsearch", "indexing",
        "recommendation engine", "ranking",
    ),
}

_KEYWORD_TO_FAMILY: dict[str, str] = {
    kw: fam for fam, kws in TECH_FAMILIES.items() for kw in kws
}
# Longest keywords first so "machine learning" wins before "learning" variants.
_KEYWORD_RES: list[tuple[str, re.Pattern]] = [
    (kw, re.compile(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", re.I))
    for kw in sorted(_KEYWORD_TO_FAMILY, key=len, reverse=True)
]

_SECTION_HEADER_RE = re.compile(r"^[A-Z\s&/]{3,40}$")   # "EXPERIENCE", "SKILLS"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _keywords(text: str) -> set[str]:
    """Known tech keywords present in the text."""
    t = text or ""
    return {kw for kw, rx in _KEYWORD_RES if rx.search(t)}


def extract_keywords(text: str) -> set[str]:
    """Public taxonomy keyword scan — also used by the draft quality scorer."""
    return _keywords(text)


def _families(kws: set[str]) -> set[str]:
    return {_KEYWORD_TO_FAMILY[k] for k in kws}


def extract_facts(resume: str, max_facts: int = 40) -> list[str]:
    """Individual résumé facts: bullet lines, else sentences. Headers skipped."""
    facts: list[str] = []
    for raw in (resume or "").splitlines():
        line = raw.strip().lstrip("-•*–—· ").strip()
        if not (25 <= len(line) <= 240):
            continue
        if _SECTION_HEADER_RE.match(line):
            continue
        facts.append(line)
    if len(facts) < 3:   # unstructured résumé — fall back to sentences
        for s in _SENTENCE_SPLIT_RE.split(resume or ""):
            s = s.strip()
            if 25 <= len(s) <= 240 and s not in facts:
                facts.append(s)
    return facts[:max_facts]


def rank_relevant_facts(
    resume: str, *, context: str = "", designation: str = "", company: str = "",
    top_n: int = 4, variety_seed: str = "",
) -> tuple[list[str], list[str]]:
    """
    Returns (relevant_facts, shared_signals).

    relevant_facts — up to top_n résumé facts that align with the recipient
    (direct keyword overlap counts 3×, same-family overlap 1×), best first.
    shared_signals — the recipient-side keywords that drove the match (for the
    subject line and the "why these" note). Both empty when there's no signal.

    variety_seed (contact identity) rotates facts WITHIN equal-score groups
    only — similar companies get varied openers, but the single best match for
    this recipient is never demoted below a weaker one.
    """
    profile_kws = _keywords(f"{context}\n{designation}\n{company}")
    if not profile_kws:
        return [], []
    profile_fams = _families(profile_kws)

    scored: list[tuple[int, int, str, set[str]]] = []
    for i, fact in enumerate(extract_facts(resume)):
        fact_kws = _keywords(fact)
        if not fact_kws:
            continue
        direct = fact_kws & profile_kws
        fams = _families(fact_kws) & profile_fams
        score = 3 * len(direct) + len(fams)
        if score > 0:
            scored.append((score, i, fact, direct))

    if not scored:
        return [], []
    scored.sort(key=lambda t: (-t[0], t[1]))   # score desc, résumé order as tiebreak
    top = scored[:top_n]

    if variety_seed:
        # Rotate within each equal-score run, then reassemble in score order.
        regrouped: list[tuple[int, int, str, set[str]]] = []
        group: list[tuple[int, int, str, set[str]]] = []
        for item in top:
            if group and item[0] != group[0][0]:
                regrouped.extend(rotate_for_variety(group, variety_seed))
                group = []
            group.append(item)
        regrouped.extend(rotate_for_variety(group, variety_seed))
        top = regrouped

    shared: list[str] = []
    for _, _, _, direct in top:
        for kw in sorted(direct):
            if kw not in shared:
                shared.append(kw)
    if not shared:   # family-only match — name the recipient-side signals instead
        shared = sorted(profile_kws)[:4]
    return [fact for _, _, fact, _ in top], shared[:5]


def rotate_for_variety(items: list, seed: str) -> list:
    """Deterministically rotate a list per contact, so five emails to similar
    companies don't all open with the identical project. Seeded by contact
    identity — same contact always gets the same email. Callers pass only
    equal-relevance groups, so rotation never demotes a better match."""
    if len(items) < 2:
        return items
    offset = int(md5(seed.encode()).hexdigest(), 16) % len(items)
    return items[offset:] + items[:offset]
