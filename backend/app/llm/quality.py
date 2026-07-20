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
    # Second-person assertions about what the recipient is DOING — the exact
    # shape the model copies from the prompt's example opener when it has no
    # real context ("You're rebuilding the payments service at Fly.io").
    # Present-continuous ("you're building X") and recent-event ("you've
    # shipped X"). Grounding then decides: kept when the payload word is in the
    # verified context, stripped (and regenerated) when the context is empty.
    re.compile(r"\byou(?:'re| are)\s+(?:currently\s+|now\s+|just\s+|recently\s+)?\w+ing\b", re.I),
    re.compile(r"\byou(?:'ve| have)\s+(?:just\s+|recently\s+)?(?:launched|shipped|raised|built|announced|hired|rebuilt|migrated|scaled|expanded|rewrote|adopted|moved|switched)\b", re.I),
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
        rf"\b{re.escape(c)}\b.{{0,50}}\b(uses?|is|are|has|have|recently|just|announced|raised|launched|shipped|ships|builds?|building|shipping|scaling|launching|hiring|announcing|raising|migrating|rewriting)\b",
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


# ── Candidate-number grounding ────────────────────────────────────────────────
# The company-claim scrub can't catch the model inventing numbers about the
# CANDIDATE ("reduced latency to under 500ms" when the résumé never says 500ms).
# A made-up metric survives until an interviewer asks about it. Strong numbers
# (percentages, money, latency, scale) in the body must have their digits
# present somewhere in the résumé/context; small bare integers ("15-minute
# chat", "one of two") are ignored.

_STRONG_NUM_RE = re.compile(
    r"\d[\d,.]*\s*(?:%|percent|ms\b|milliseconds?|x\b|k\b|m\b|b\b|"
    r"million|billion|thousand|users|queries|requests|transactions|"
    r"events|rows|records|services|rps|qps)"
    r"|[$€£]\s*\d[\d,.]*"
    r"|\d[\d,.]*\s*(?:/|per\s+)(?:yr|year|month|week|day|sec|second)",
    re.I,
)
_DIGITS_RE = re.compile(r"\d+")

# Spelled-out counts slip past _STRONG_NUM_RE entirely ("took three rewrites"
# has no digit character). Small models invent these freely — same failure
# class as invented digits, just undetectable by the digit check above. Cover
# 1-12 against common achievement nouns; converted to its digit form below so
# the SAME grounding set (résumé/context digit tokens) decides if it's real.
_WORD_TO_DIGIT = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}
_STRONG_WORDNUM_RE = re.compile(
    r"\b(" + "|".join(_WORD_TO_DIGIT) + r")\s+"
    r"(rewrites?|iterations?|attempts?|outages?|incidents?|migrations?|"
    r"launches?|deploys?|deployments?|releases?|sprints?|prototypes?|"
    r"engineers?|hires?|customers?|clients?|projects?|teams?|rounds?|"
    r"tries|years?|months?|weeks?|days?|hours?|times)\b",
    re.I,
)


def _digit_tokens(text: str) -> set[str]:
    return set(_DIGITS_RE.findall(text or ""))


def scrub_ungrounded_numbers(body: str, ground_text: str) -> tuple[str, list[str]]:
    """Remove sentences containing strong numeric claims whose digits appear
    nowhere in the grounding corpus (résumé + context). Returns
    (clean_body, offending_sentences)."""
    ground = _digit_tokens(ground_text)
    flagged: list[str] = []
    out_paragraphs: list[str] = []
    for para in (body or "").split("\n\n"):
        kept_lines: list[str] = []
        for line in para.split("\n"):
            kept = []
            for sentence in _SENTENCE_SPLIT_RE.split(line):
                bad = False
                for m in _STRONG_NUM_RE.finditer(sentence):
                    if any(d not in ground for d in _DIGITS_RE.findall(m.group())):
                        bad = True
                        break
                if not bad:
                    for m in _STRONG_WORDNUM_RE.finditer(sentence):
                        if _WORD_TO_DIGIT[m.group(1).lower()] not in ground:
                            bad = True
                            break
                if bad:
                    flagged.append(sentence.strip())
                    continue
                kept.append(sentence)
            joined = " ".join(s for s in kept if s.strip()).strip()
            if joined or not line.strip():
                kept_lines.append(joined)
        para_out = "\n".join(kept_lines).strip()
        if para_out:
            out_paragraphs.append(para_out)
    return "\n\n".join(out_paragraphs), flagged


# ── Filler removal + reply-worthiness scoring ─────────────────────────────────
# Cover-letter sentences carry zero information ("This experience taught me the
# importance of..."). Recipients pattern-match them instantly and stop reading.
# Removal is meaning-safe by construction: only sentences matching an explicit
# filler pattern are cut, never anything judged "abstract" heuristically.

_FILLER_RES = (
    re.compile(r"^this experience (?:taught|has taught|showed)\b", re.I),
    re.compile(r"^i(?:'m| am) confident\b", re.I),
    re.compile(r"^i believe (?:that )?(?:i|my)\b", re.I),
    re.compile(r"^i(?:'d| would) (?:love|welcome) the opportunity\b", re.I),
    re.compile(r"^i(?:'d| would) like to (?:share|highlight|mention|express)\b", re.I),
    re.compile(r"^my (?:skills|experience|background)\b.{0,60}\b(?:make|makes|align|aligns|position|positions)\b", re.I),
    re.compile(r"^i(?:'m| am) (?:intrigued|fascinated) by\b", re.I),
    re.compile(r"^(?:overall|in summary|in conclusion),?\b", re.I),
    re.compile(r"^i look forward to\b", re.I),
    re.compile(r"\bwas impressed by your\b", re.I),
    re.compile(r"\byour (?:team's|company's) (?:commitment|dedication) to\b", re.I),
    # "I'd be happy to discuss how my experience could apply / tackle similar
    # challenges together" — the content-free hedge the model closes on instead
    # of a real ask (seen in the engineering_leader sample).
    re.compile(r"^i(?:'d| would) be (?:happy|glad|delighted) to (?:discuss|chat|talk|connect|share)\b", re.I),
    re.compile(r"\bhow my (?:experience|background|skills|work) (?:could|might|would|may|can) (?:apply|help|be relevant|contribute)\b", re.I),
    # "let me know about any opportunities that align with my skills and
    # experience" — the generic mass-mail ask the recruiter template was
    # rewritten to avoid; stripping it forces has_ask=False so a real,
    # specific CTA gets regenerated instead of shipping this one.
    re.compile(r"\bopportunit(?:y|ies)\b[^.]{0,60}\balign(?:s|ing)?\s+with\s+my\b", re.I),
)


def strip_filler(body: str) -> tuple[str, int]:
    """Remove cover-letter filler sentences. Returns (clean_body, removed_count).
    Paragraph structure preserved; empty paragraphs dropped."""
    removed = 0
    out_paragraphs: list[str] = []
    for para in (body or "").split("\n\n"):
        kept_lines: list[str] = []
        for line in para.split("\n"):
            kept = []
            for sentence in _SENTENCE_SPLIT_RE.split(line):
                if sentence.strip() and any(rx.search(sentence.strip()) for rx in _FILLER_RES):
                    removed += 1
                    continue
                kept.append(sentence)
            joined = " ".join(s for s in kept if s.strip()).strip()
            if joined or not line.strip():
                kept_lines.append(joined)
        para_out = "\n".join(kept_lines).strip()
        if para_out:
            out_paragraphs.append(para_out)
    return "\n\n".join(out_paragraphs), removed


# Phrases that mark a draft as a mass cover letter — each hit costs points even
# when it isn't a whole strippable sentence.
_COVER_LETTER_PHRASES = (
    "i'd like to share", "i would like to share", "i'm confident", "i am confident",
    "i believe my", "contribute to your team", "the opportunity to",
    "this experience taught me", "commitment to", "impressed by", "intrigued by",
    "my skills", "well-positioned", "value i can bring", "make an impact",
    "i look forward", "i am writing", "i wanted to reach out",
    "hope this finds you well", "proven track record", "great fit", "perfect fit",
)

_SELF_OPENER_RE = re.compile(r"^(?:i|i'm|i've|i'd|my|as an?)\b", re.I)
_PROPER_NOUN_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]{2,}")

# Unambiguous machine-writing single-word tells (the prompt bans these but the
# model still reaches for them). Word-boundary matched so 'event-driven' and
# 'delving' don't false-trip; ambiguous tech words (driven/skilled/robust as in
# 'robustness') are deliberately excluded. Each hit docks the reply score so a
# tell-heavy draft drops below the bar and regenerates.
_BANNED_TELLS = (
    "excited", "passionate", "thrilled", "leverage", "seamless", "synergy",
    "delve", "showcase", "testament", "resonate", "cutting-edge", "seasoned",
    "well-positioned", "utilize", "spearheaded", "elevate",
)
_BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _BANNED_TELLS) + r")\b", re.I)


def count_banned(text: str) -> int:
    return len(_BANNED_RE.findall(text or ""))


def score_draft(body: str, subject: str = "", *,
                word_range: tuple[int, int] = (60, 120),
                context: str = "", company: str = "") -> int:
    """
    Deterministic reply-worthiness score, 0–100. Not a style opinion — each
    deduction maps to a known reply-rate killer:
      length outside the band · cover-letter phrases · a me-first opener ·
      information-free sentences · no closing question · a bloated subject.
    Used to pick the better of two generations and to trigger a retry.
    """
    b = (body or "").strip()
    if not b:
        return 0
    score = 100
    low = b.lower()

    lo, hi = word_range
    n_words = len(b.split())
    if n_words > hi:
        score -= min(30, n_words - hi)
    elif n_words < lo:
        score -= min(30, 2 * (lo - n_words))

    score -= 12 * sum(low.count(p) for p in _COVER_LETTER_PHRASES)
    score -= 10 * count_banned(b)   # unambiguous AI-tell vocabulary

    # Opener: about THEM (company name / their tech) good, about "I/my" bad.
    first_line = next((ln for ln in b.splitlines() if ln.strip()), "")
    first_sentence = _SENTENCE_SPLIT_RE.split(first_line)[0].strip()
    if _SELF_OPENER_RE.match(first_sentence):
        score -= 12
    fs_low = first_sentence.lower()
    comp_tok = (company or "").split()[0].lower() if (company or "").strip() else ""
    if comp_tok and comp_tok in fs_low:
        score += 6
    if context:
        from app.llm.relevance import extract_keywords
        if any(k in fs_low for k in extract_keywords(context)):
            score += 6

    # Fact density: sentences with no digit and no proper noun carry nothing.
    flat = re.sub(r"\s+", " ", b)
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(flat) if len(s.split()) >= 4]
    factless = sum(
        1 for s in sentences
        if not re.search(r"\d", s) and not _PROPER_NOUN_RE.search(s)
    )
    score -= min(20, 5 * factless)

    if not ends_with_question(b):
        score -= 10

    s_low = (subject or "").lower()
    if subject:
        if len(subject.split()) > 8:
            score -= 5
        if any(w in s_low for w in ("application", "opportunity", "job inquiry")):
            score -= 8

    return max(0, min(100, score))
