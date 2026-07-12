"""
Post-generation quality pass — deterministic and invisible.

The prompt bans fabrication, but small models fabricate anyway ("I noticed
{company} uses Postgres" inferred from the CANDIDATE's stack). One invented
claim sent to a real CTO burns that contact forever, so claims about the
recipient's company are verified mechanically against the stored context:

  1. find sentences that CLAIM knowledge about the company
  2. check each claim's content words actually appear in the verified context
  3. ungrounded claim → the generator retries once; if the retry fabricates
     too, the offending sentences are silently stripped

The user never sees any of this — drafts just stop lying.
"""

import re

# Sentence-level patterns that assert knowledge about the recipient/company.
# Candidate-side statements ("I built", "I cut latency 40%") never match.
_CLAIM_RES = (
    re.compile(r"\bi(?:'ve| have)? (?:noticed|saw|see|read|came across|found|learned|heard|hear)\b", re.I),
    re.compile(r"\bi(?:'ve| have) been (?:following|watching|reading|tracking)\b", re.I),
    re.compile(r"\byour (?:team|company|platform|product|stack|engineering team|codebase|api|app)(?:'s)?\s+(?:uses?|is|are|has|have|ships?|runs?|built|builds?|recently|focus)\b", re.I),
    re.compile(r"\bcongrat(?:s|ulations)\b", re.I),
    re.compile(r"\bimpressive|impressed\b", re.I),
)

# Words that carry no verifiable content — excluded from grounding checks.
_STOPWORDS = frozenset({
    "that", "this", "with", "your", "youre", "you", "team", "company", "the",
    "and", "for", "about", "have", "has", "been", "recently", "just", "how",
    "what", "was", "were", "are", "its", "their", "they", "from", "into",
    "really", "very", "over", "under", "more", "some", "work", "working",
    "building", "build", "built", "product", "platform", "focus", "focused",
    "noticed", "saw", "read", "came", "across", "found", "learned", "impressive",
    "impressed", "congrats", "congratulations", "using", "uses", "use",
})

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _company_claim_re(company: str) -> re.Pattern | None:
    """'{Company} uses/raised/launched …' — a direct assertion about them."""
    c = (company or "").strip()
    if not c or c.lower() == "unknown":
        return None
    return re.compile(
        rf"\b{re.escape(c)}\b.{{0,50}}\b(uses?|is|are|has|have|recently|just|announced|raised|launched|shipped|ships|builds?)\b",
        re.I,
    )


def _is_claim(sentence: str, company_re: re.Pattern | None) -> bool:
    if any(rx.search(sentence) for rx in _CLAIM_RES):
        return True
    return bool(company_re and company_re.search(sentence))


def _grounded(sentence: str, ctx_lower: str, company: str) -> bool:
    """Does the claim's payload actually appear in the verified context?

    Prefix matching (first 5 chars of each content word) tolerates inflection
    differences: body "PostgreSQL" still matches context "Postgres".
    """
    if not ctx_lower.strip():
        return False   # no context at all — every company claim is invented
    comp = (company or "").lower()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9'\-]{3,}", sentence.lower())
    payload = [w for w in words if w not in _STOPWORDS and w != comp and w not in comp]
    if not payload:
        return False   # pure fluff claim ("I'm impressed by your team")
    return any(w[:5] in ctx_lower for w in payload)


def scrub_fabrications(body: str, company: str = "", context: str = "") -> tuple[str, list[str]]:
    """
    Return (clean_body, fabricated_sentences).

    fabricated_sentences non-empty means the body contained company claims not
    grounded in the context; clean_body has them removed (paragraph structure
    preserved, empty paragraphs dropped).
    """
    ctx_lower = (context or "").lower()
    company_re = _company_claim_re(company)
    fabricated: list[str] = []
    out_paragraphs: list[str] = []

    for para in (body or "").split("\n\n"):
        kept: list[str] = []
        for line in para.split("\n"):
            kept_sentences = []
            for sentence in _SENTENCE_SPLIT_RE.split(line):
                if sentence.strip() and _is_claim(sentence, company_re) \
                        and not _grounded(sentence, ctx_lower, company):
                    fabricated.append(sentence.strip())
                    continue
                kept_sentences.append(sentence)
            joined = " ".join(s for s in kept_sentences if s.strip()).strip()
            if joined or not line.strip():
                kept.append(joined)
        para_out = "\n".join(kept).strip()
        if para_out:
            out_paragraphs.append(para_out)

    return "\n\n".join(out_paragraphs), fabricated


def ends_with_question(body: str) -> bool:
    """The ask must land as the closing question — used as a retry signal only
    (never worth stripping content over)."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    return bool(lines) and lines[-1].rstrip('."”’)').endswith("?")
