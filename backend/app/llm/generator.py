"""

Email generator — thin LangChain wrapper around the configured LLM.

Provider is resolved once on first use, then cached.



The LLM writes ONLY the message body. The greeting ("Hi {first name},") and the

sign-off ("Best regards,\n{sender name}") are wrapped on deterministically so the

format is identical every time and the names are never hallucinated.

"""



import asyncio

import logging

import re

from langchain_core.prompts import ChatPromptTemplate

from langchain_core.output_parsers import StrOutputParser

from langchain_core.language_models import BaseChatModel



from app.llm.factory import detect_provider, create_llm

from app.llm.prompts import TEMPLATES, WORD_RANGES, FORMAL_KEYS, get_designation_key

from app.llm.parsing import parse_subject_body

from app.llm.quality import (

    scrub_fabrications, scrub_ungrounded_numbers, ends_with_question,

    strip_filler, score_draft,

)

from app.llm.relevance import rank_relevant_facts

from app.scrapers.base import plausible_person_name



log = logging.getLogger(__name__)



# Greeting words an LLM might prepend despite instructions — stripped defensively.

_GREETING_RE = re.compile(

    r"^\s*(hi|hey|hello|dear|greetings)\b[^\n]*\n+", re.IGNORECASE

)

# Sign-off words that begin a closing block — everything from here down is dropped.

# Deliberately excludes "looking forward" / "talk soon": those are often the

# legitimate last line of the body (a soft CTA), not a sign-off.

_SIGNOFF_RE = re.compile(

    # Bare 'best' / 'regards' must be alone on the line (followed only by optional

    # punctuation/whitespace) to avoid matching "Best practices..." or "Regards your...".

    r"\n+\s*(best regards|warm regards|kind regards|all the best|yours truly|"

    r"thanks|thank you|cheers|sincerely|warmly"

    r"|best(?=\s*[,.]?\s*$)|regards(?=\s*[,.]?\s*$))\s*,?\s*[\s\S]*$",

    re.IGNORECASE | re.MULTILINE,

)

_PLACEHOLDER = "[MOCK DRAFT"   # mock output marker — never wrap/sign these



# Subject lines sometimes come back with surrounding quotes — strip them.

_QUOTED_SUBJECT_RE = re.compile(r'^[""‘](.*)[""’]$')



# ── Deterministic de-AI pass ─────────────────────────────────────────────────

# The model emits these machine-writing tells even when the prompt bans them.

# Recipients who read hundreds of emails pattern-match them instantly, so they

# are removed mechanically. Meaning-safe transforms only.



_CURLY_MAP = str.maketrans({0x201c: '"', 0x201d: '"', 0x2018: "'", 0x2019: "'"})

# Numeric ranges like "150k—200k" or "$150—$200" keep a plain hyphen.

# Lookbehind covers both digit ("$150—“) and ‘k’ suffix (“150k—“).

_NUM_DASH_RE = re.compile(r"(?<=[\dk])\s*[—–]\s*(?=[$\d])", re.IGNORECASE)

_DASH_RE     = re.compile(r"(?<=[\w.,%)\"'])\s*[—–]\s*")



# Whole-sentence pleasantries that add nothing — dropped wherever they appear.

# Each pattern targets an exact sentence; the (?:^|\\.\\s*) prefix matches at

# the start of the string or after a sentence-ending period + whitespace so

# mid-body pleasantries are caught, not only leading ones.

_PLEASANTRY_RES = (

    re.compile(

        r"(?:(?<=\.)\s+|^)i hope this (?:e-?mail |message |note )?finds you well[.!,]?\s*",

        re.IGNORECASE,

    ),

    re.compile(

        r"(?:(?<=\.)\s+|^)i hope you(?:'re| are) (?:doing |having )?(?:well|great|a great week)[.!,]?\s*",

        re.IGNORECASE,

    ),

    re.compile(

        r"(?:(?<=\.)\s+|^)i(?:'m| am) (?:writing|reaching out) to\b[^.!?]*[.!?]?\s*",

        re.IGNORECASE,

    ),

)

# Openers where cutting the lead-in leaves a grammatical sentence:

# "I wanted to reach out because I built X" → "I built X"

# "I noticed that your team ships fast"     → "Your team ships fast"

_OPENER_RES = (

    re.compile(r"(?i)^i wanted to reach out (?:because|since|as)\s+"),

    re.compile(r"(?i)^i(?: just| recently)? (?:noticed|saw|know) that\s+"),

)





def _humanize(text: str, formal: bool = False) -> str:

    """Mechanically remove machine-writing tells from a generated body.

    Formal register keeps its courtesy lines and structured phrasing — only

    the universal cleanups apply (quote normalization, dashes, exclamations).

    """

    t = text.translate(_CURLY_MAP)

    t = _NUM_DASH_RE.sub("-", t)

    if formal:

        t = re.sub(r"\s*[—–]\s*", " - ", t)

    else:

        t = _DASH_RE.sub(", ", t)

    t = t.replace("!", ".")            # exclamation marks read as fake enthusiasm

    if not formal:

        for rx in _PLEASANTRY_RES:

            t = rx.sub("", t).lstrip()

        for rx in _OPENER_RES:

            stripped = rx.sub("", t, count=1)

            if stripped != t and stripped:

                t = stripped[0].upper() + stripped[1:]

    t = re.sub(r"[ \t]{2,}", " ", t)

    t = re.sub(r"\n{3,}", "\n\n", t)

    return t.strip()





# Names that are placeholders or role-inbox localparts, never a real first name.

# Greeting "Hi Hr," / "Hi Jobs," torches credibility — always fall back to "Hi,".

_NOT_A_NAME = frozenset({

    "contact", "there", "team", "hiring", "manager", "hiring manager", "recruiter",

    "founder", "hr", "talent", "jobs", "careers", "career", "recruiting", "people",

    "info", "hello", "support", "admin", "sales", "office", "mail", "webmaster",

    "help", "work", "apply", "hi", "hey", "email", "inbox", "general", "unknown",

    "user", "test", "demo", "example", "sample", "someone", "name", "firstname",

    "candidate", "applicant", "recruitment", "notifications", "automated", "staff",

    "department", "dept", "account", "accounts", "crew", "everyone", "folks",

})


# Companies that can't be greeted or named: the "Unknown" sentinel, empty, or

# scraped garbage too long to be a brand ("Hi Some Scraped Legalese team,").

def _usable_company(company: str) -> str:

    c = " ".join((company or "").split())

    if not c or c.lower() in ("unknown", "n/a", "na", "-") or len(c) > 24:

        return ""

    return c





def _first_name(name: str, company: str = "") -> str:

    """Extract a usable first name, or '' if the contact name is a placeholder.



    Runs the full person-name plausibility check first (same one the hunt uses),

    so org names ("Acme Careers"), handles ("dev4life"), role titles and test

    fixtures never make it into a greeting — even on hand-added contacts that

    skipped the hunt's sanitization.

    """

    n = (name or "").strip()

    if not n or n.lower() in _NOT_A_NAME:

        return ""

    if not plausible_person_name(n, company):

        return ""

    first_raw = re.split(r"\s+", n)[0]

    if re.search(r"\d", first_raw):

        return ""   # usernames like "jsmith84" — never greet with these

    # Drop anything not part of a name token, keeping apostrophes/hyphens so
    # "D'Angelo" / "Anne-Marie" survive intact.

    first = re.split(r"[^A-Za-z'\-]", first_raw)[0]

    if len(first) < 2 or first.lower() in _NOT_A_NAME:

        return ""

    return first.capitalize()





def _strip_affixes(body: str) -> str:

    """Remove any greeting line or sign-off block the LLM added on its own."""

    body = _GREETING_RE.sub("", body, count=1)

    body = _SIGNOFF_RE.sub("", body)

    return body.strip()





def _wrap(body: str, contact_name: str, sender_name: str, sender_links: str = "",

          company: str = "", formal: bool = False) -> str:

    """Add a deterministic greeting and 'Best regards,' sign-off.



    Greeting tiers — always honest about what we actually know:

      real person name      → "Hi Priya,"

      no name, known company → "Hi Vercel team,"   (role inboxes, unnamed leads)

      nothing usable         → "Hi,"

    A "name" that is really the company ("Vercel" at Vercel) counts as no name —

    greeting a brand like a person ("Hi Vercel,") torches credibility.



    sender_links, when set, becomes one line under the name — GitHub/LinkedIn/

    portfolio. For a job-seeking email this is the click-through that turns

    "who is this?" into an interview.

    """

    if _PLACEHOLDER in body:

        return body.strip()

    core = _humanize(_strip_affixes(body), formal=formal)

    first = _first_name(contact_name, company)

    comp = _usable_company(company)

    if first and comp and first.lower() == comp.split()[0].lower():

        first = ""   # the "name" is the company — not a person

    if first:

        greeting = f"Hi {first},"

    elif comp:

        greeting = f"Hi {comp} team,"

    else:

        greeting = "Hi,"

    sender = (sender_name or "").strip()

    signoff = f"Best regards,\n{sender}" if sender else "Best regards,"

    links = " ".join((sender_links or "").split())

    if links:

        signoff += f"\n{links}"

    return f"{greeting}\n\n{core}\n\n{signoff}"





def _clean_subject(subject: str, company: str = "", formal: bool = False) -> str:

    """Normalise the LLM-generated subject line. Formal subjects keep their

    application-style casing; direct subjects get the internal-note treatment."""

    s = subject.strip()

    if _PLACEHOLDER in s:

        return s   # mock marker must survive untouched

    s = s.translate(_CURLY_MAP)

    # Remove surrounding quotes the LLM sometimes adds

    m = _QUOTED_SUBJECT_RE.match(s)

    if m:

        s = m.group(1).strip()

    # Strip an accidental "Re:" the LLM sometimes adds to non-followups

    if s.lower().startswith("re: re:"):

        s = s[4:].strip()

    if formal:

        # Formal application subjects keep their casing and their dash

        # ("SDE Application - Priya Nair") — normalize dash style only.

        s = re.sub(r"\s*[—–]\s*", " - ", s)

        return s.rstrip("!.").strip()

    s = _DASH_RE.sub(", ", s)

    s = s.rstrip("!.")   # trailing bang/period reads as odd in a subject line

    # Truncate gracefully if way over the requested 6 words

    words = s.split()

    if len(words) > 9:

        s = " ".join(words[:7])

        words = s.split()

    # De-Title-Case: Title Case Every Word reads as a campaign blast; lowercase

    # reads as a note someone typed. Acronyms, digit tokens, and the company

    # name keep their casing.

    if len(words) >= 3:

        capped = sum(1 for w in words if w[:1].isupper())

        if capped / len(words) >= 0.6:

            comp_tokens = {t.lower() for t in (company or "").split()}

            s = " ".join(

                w if (w.isupper() or any(ch.isdigit() for ch in w)

                      or w.lower() in comp_tokens)

                else w.lower()

                for w in words

            )

    return s.strip()





# Shortest acceptable body core (pre-greeting/sign-off). Anything under this is

# a malformed generation (bare "BODY:", refusal fragment, empty string), not an email.

_MIN_BODY_CHARS = 40





class EmailGenerator:

    """

    Generates cold emails and follow-ups using any LangChain-compatible LLM.

    LLM is lazily initialised on first call.

    """



    def __init__(self):

        self._llm: BaseChatModel | None = None

        self._chains: dict = {}

        self._init_lock = asyncio.Lock()



    async def _ensure_llm(self) -> None:

        if self._llm is None:

            async with self._init_lock:

                if self._llm is None:   # double-checked inside the lock

                    provider, model = await detect_provider()

                    self._llm = create_llm(provider, model)

                    log.info(f"EmailGenerator using {provider}/{model}")



    def _get_chain(self, key: str):

        if key not in self._chains:

            prompt = ChatPromptTemplate.from_template(TEMPLATES[key])

            self._chains[key] = prompt | self._llm | StrOutputParser()  # type: ignore[operator]

        return self._chains[key]



    @staticmethod

    def _trim_resume(text: str, limit: int = 3000) -> str:

        """Truncate resume to `limit` chars without cutting mid-word."""

        if len(text) <= limit:

            return text

        cut = text[:limit].rsplit(None, 1)[0]   # rsplit on whitespace, drop partial word

        return cut



    # A draft scoring below this is regenerated once; the best of the two

    # attempts ships. 70 ≈ "no cover-letter phrases, decent opener, right length".

    _QUALITY_BAR = 70



    async def _invoke_checked(

        self, chain, variables: dict, *, contact_name: str, sender_name: str,

        sender_links: str = "", company: str = "", context: str = "",

        word_range: tuple[int, int] = (60, 120), ground_numbers: str = "",

        formal: bool = False,

    ) -> tuple[str, str]:

        """

        Invoke the chain, run the invisible quality pipeline, and return

        (subject, wrapped_body) for the BEST attempt:



          1. strip model-added greetings/sign-offs

          2. scrub ungrounded company claims (fact-check against context)

          3. scrub invented candidate numbers (grounded against résumé/context)

          4. DIRECT register only: cut cover-letter filler and score

             reply-worthiness (opener, fact density, length, closing question)

          5. below the bar → regenerate once, then keep whichever attempt

             scored higher (attempt 1 is never thrown away for a worse retry)



        Formal register (hiring inboxes, recruiters) keeps its application

        structure — fact-checking still applies, style scrubbing doesn't.



        Raises RuntimeError if both attempts produce garbage — better a clear

        error than a blank draft that could be bulk-sent.

        """

        candidates: list[tuple[int, str, str]] = []   # (score, subject, clean_body)

        for attempt in (1, 2):

            raw = await chain.ainvoke(variables)

            subject, body = parse_subject_body(raw)

            if _PLACEHOLDER in raw:

                return (_clean_subject(subject, company, formal),

                        _wrap(body, contact_name, sender_name, sender_links, company, formal))



            clean, fabricated = scrub_fabrications(

                _strip_affixes(body), company=company, context=context,

            )

            bad_numbers: list[str] = []

            if ground_numbers:

                clean, bad_numbers = scrub_ungrounded_numbers(clean, ground_numbers)

            if formal:

                n_filler, quality = 0, 100

                below_bar = bool(fabricated or bad_numbers)

            else:

                clean, n_filler = strip_filler(clean)

                quality = score_draft(clean, subject, word_range=word_range,

                                      context=context, company=company)

                below_bar = bool(

                    fabricated or bad_numbers or n_filler

                    or not ends_with_question(clean) or quality < self._QUALITY_BAR

                )

            if len(clean.strip()) >= _MIN_BODY_CHARS:

                candidates.append((quality, subject, clean))

            if attempt == 1 and (not candidates or below_bar):

                log.info(

                    f"LLM draft below bar on attempt 1 (formal={formal}, score={quality}, "

                    f"ungrounded={len(fabricated)}, invented_numbers={len(bad_numbers)}, "

                    f"filler={n_filler}) — regenerating"

                )

                continue

            break



        if not candidates:

            raise RuntimeError(

                "The LLM returned an empty or malformed draft twice in a row. "

                "Try again, or check the provider/model via /api/health."

            )

        quality, subject, body = max(candidates, key=lambda c: c[0])

        if len(candidates) > 1:

            log.info(f"LLM draft: kept best of {len(candidates)} attempts (score={quality})")

        wrapped = _wrap(body, contact_name, sender_name, sender_links, company, formal)

        return _clean_subject(subject, company, formal), wrapped



    async def generate(

        self,

        *,

        name: str,

        designation: str,

        company: str,

        resume: str,

        company_context: str = "",

        source: str = "",

        sender_name: str = "",

        sender_links: str = "",

        has_attachment: bool = False,

    ) -> str:

        await self._ensure_llm()

        key = get_designation_key(designation)

        formal = key in FORMAL_KEYS

        chain = self._get_chain(key)



        if company_context.strip():

            ctx_block = (

                "\nVERIFIED CONTEXT about this recipient/company "

                "(this is REAL — build the email around THIS, do not invent any other facts):\n"

                f"{company_context.strip()[:2000]}\n\n"

            )

        else:

            ctx_block = (

                "\n(No verified context available. Do NOT invent product names, funding rounds, "

                "metrics, or tech stack. Anchor purely on the candidate's own background and an "

                "honest, direct reason for reaching out to this company.)\n\n"

            )



        # Relevance pre-pass: rank the résumé's individual facts against what

        # THIS recipient cares about (their job context, role, company) and hand

        # the model an explicit shortlist. Otherwise small models build every

        # email around the same flagship project — an AI company should see the

        # candidate's LLM work first, a payments company the billing system.

        # Rotation is seeded per contact so similar companies still get varied

        # openers. No signal → no shortlist → résumé passed through as before.

        resume_for_prompt = self._trim_resume(resume)

        relevant, shared = rank_relevant_facts(

            resume_for_prompt, context=company_context,

            designation=designation, company=company,

            variety_seed=f"{name}|{company}",

        )

        if relevant:

            bullets = "\n".join(f"- {f}" for f in relevant)

            resume_for_prompt = (

                "MOST RELEVANT background for THIS recipient — their role/company "

                f"signals ({', '.join(shared)}) directly match these. Build the "

                "email around one or two of THESE, not the candidate's flagship "

                "project:\n"

                f"{bullets}\n\n"

                f"Full résumé:\n{resume_for_prompt}"

            )



        # Never leak junk into the prompt: a placeholder name ("Contact",

        # "dev4life") or the "Unknown" company sentinel would get echoed into

        # the body/subject by the model ("referral at Unknown?").

        prompt_name = name if _first_name(name, company) else (

            "unknown (never refer to the recipient by any name)"

        )

        comp = _usable_company(company)

        prompt_company = comp or (

            "their company (name unknown — never write 'their company' or "

            "'Unknown' in the subject or body; phrase around it, e.g. 'your team')"

        )

        subject, body = await self._invoke_checked(

            chain,

            {

                "name":          prompt_name,

                "designation":   designation,

                "company":       prompt_company,

                "resume":        resume_for_prompt,

                "context_block": ctx_block,

                "source_hint":   _source_hint(source),

                "sender_name":   (sender_name or "").strip() or "the candidate",

                # Only formal templates reference this; the résumé line must

                # never appear unless a file will actually be attached.

                "attachment_note": (

                    "\n5. Mention that the resume is attached for review."

                    if has_attachment and formal else ""

                ),

            },

            contact_name=name, sender_name=sender_name, sender_links=sender_links,

            company=company, context=company_context,

            word_range=WORD_RANGES.get(key, (60, 120)),

            ground_numbers=f"{resume}\n{company_context}",

            formal=formal,

        )

        return f"SUBJECT: {subject}\n\nBODY:\n{body}"



    async def generate_followup(

        self,

        *,

        name: str,

        company: str,

        original_email: str,

        sender_name: str = "",

        sender_links: str = "",

        context: str = "",

    ) -> str:

        await self._ensure_llm()

        chain = self._get_chain("followup")



        # Parse the original BEFORE truncating, so the subject survives intact.

        orig_subject, orig_body = parse_subject_body(original_email)



        if context.strip():

            ctx_block = (

                "\nVERIFIED CONTEXT about this company (real facts — the new hook "

                "may draw on ONE detail from here that the original email didn't use):\n"

                f"{context.strip()[:800]}\n"

            )

        else:

            ctx_block = ""



        subject, body = await self._invoke_checked(

            chain,

            {

                "name":           name if _first_name(name, company) else "unknown (never use a name)",

                "company":        _usable_company(company) or "their company (name unknown — never write it literally)",

                "original_email": (orig_body or original_email)[:600],

                "context_block":  ctx_block,

            },

            contact_name=name, sender_name=sender_name, sender_links=sender_links,

            company=company,

            # Grounding corpus for the follow-up: the verified context PLUS the

            # original email (repeating an already-vetted claim isn't fabrication).

            context=f"{context}\n{orig_body or ''}",

            word_range=WORD_RANGES["followup"],

            ground_numbers=f"{context}\n{orig_body or ''}",

        )



        # Deterministic threading: the subject MUST be "Re: <original subject>" so

        # Gmail threads the follow-up under the first email. Never trust the LLM

        # here — models emit the literal placeholder or invent a new subject.

        if orig_subject:

            base = orig_subject[3:].strip() if orig_subject.lower().startswith("re:") else orig_subject

            subject = f"Re: {base}"

        return f"SUBJECT: {subject}\n\nBODY:\n{body}"



    @property

    def ready(self) -> bool:

        return self._llm is not None





def _source_hint(source: str) -> str:

    """

    Turn the provenance of a contact into an honest framing for how/why we're

    reaching out — so the opening varies by recipient instead of being generic.

    """

    s = (source or "").lower()

    if s.startswith("github"):

        return ("You found this person through their commits on GitHub — the context below "

                "names their specific repo and what it does. Reference it concretely. "

                "If they're a peer engineer (not a manager), ask for a referral or a quick "

                "chat about the team — do NOT pitch yourself as a hire to someone without "

                "hiring authority. If they ARE a technical leader, show technical depth.")

    if "hackernews" in s or s == "hn":

        return ("You found this person through their 'Who is Hiring' post on Hacker News. "

                "Reference what they said they're looking for and map yourself to it directly.")

    if "wellfound" in s:

        return ("You found this person through a Wellfound (AngelList) job listing — "

                "an early-stage startup context. Be scrappy and direct.")

    if "hunter" in s:

        return ("Reach out professionally; you don't have a specific shared touchpoint, "

                "so let the company context and your fit carry the email.")

    if any(x in s for x in ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee")):

        return ("You found this company because they're actively hiring on their ATS job board. "

                "You don't have a personal shared touchpoint — anchor the email on the specific "

                "role they're hiring for (from the context) and your direct fit for it. "

                "Be concrete about what you'd bring to that exact opening.")

    if any(x in s for x in ("remoteok", "remotive", "arbeitnow", "jobicy", "himalayas",

                             "themuse", "weworkremotely")):

        return ("You found this company through a remote-job board — they're actively hiring "

                "for a remote role. Acknowledge the remote context naturally. Lead with your "

                "strongest relevant experience and why you'd thrive working independently.")

    return ("Keep the framing honest — don't claim a connection or touchpoint you "

            "don't actually have.")





# Singleton — shared across all requests

generator = EmailGenerator()

