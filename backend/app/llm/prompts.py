"""
Cold email prompt templates, keyed by designation type.
Add new templates here without touching generator.py.

Two registers:
  - FORMAL (hiring_inbox, recruiter): a proper job application — hiring inboxes
    and recruiters expect clear, professional structure. These bypass the
    casual-voice scrubbing in the generator (FORMAL_KEYS).
  - DIRECT (everyone else): short, casual, reply-optimized — founders and
    engineers pattern-match formal mass-mail instantly and delete it.
Fabrication rules apply to both.
"""

# Shared no-fabrication block — both registers.
_NO_FABRICATION = """\
DO NOT FABRICATE - this is the most important rule:
- NEVER invent facts about the company: no made-up product names, funding rounds, metrics,
  tech stack, team size, or "I saw you shipped X" unless X appears in the context below.
- If you have no real detail about them, say something honest and direct instead.
- A generic-but-true line beats an impressive-but-invented one. Recipients can tell instantly.
- Never say a resume is attached unless the structure below explicitly tells you to."""

# Rules for the FORMAL register (hiring inboxes, recruiters).
_FORMAL_RULES = f"""\
REGISTER - a professional job application, not a marketing email:
- Clear, courteous, direct. First person. Plain professional language.
- At most ONE opening courtesy line; every other sentence must carry a fact
  (a role, a system, a number).
- No exclamation marks. No emojis. No bold text. Use "-" for any dash.
- Real numbers and real system names from the candidate's background only.

{_NO_FABRICATION}

FORMAT - write the BODY only:
- Do NOT write any greeting ("Hi", "Dear ...") - added automatically.
- Do NOT write any sign-off or signature - added automatically.
- Start directly with the first sentence. Nothing before or after the body.
- No paragraph longer than 3 sentences."""

# Rules for the DIRECT register — built around the known tells of
# machine-written email; recipients who read hundreds of emails pattern-match
# these instantly, and the email dies.
_TONE_RULES = f"""\
VOICE - sound like a person, not a language model:
- First person, contractions, plain verbs. "built" not "architected", "use" not "utilize".
- Vary sentence length. A short sentence is fine. Follow it with a longer, ordinary one.
  Do not make every line a punchy one-liner; that rhythm reads as generated.
- NO em dashes or en dashes anywhere. Use a comma, or start a new sentence.
- Prefer two items over three. A list of exactly three ("fast, reliable, and scalable")
  is the single most recognizable machine-writing pattern.
- Never end a sentence with an "-ing" add-on ("...improving reliability and reducing
  costs"). Give the result its own short sentence, with a number if one exists.
- No "not just X, but Y" / "it's not only... it's..." framings.
- No exclamation marks. No emojis. No bold text.
- Write like you'd say it out loud to a colleague. If a line would sound stiff spoken
  aloud, rewrite it plainer.

BANNED WORDS AND PHRASES (never output any of these):
"hope this finds you well", "I wanted to reach out", "I am writing to", "I came across",
"excited", "passionate", "thrilled", "leverage", "seamless", "synergy", "delve",
"showcase", "landscape", "journey", "testament", "align with", "resonate", "robust",
"cutting-edge", "fast-paced", "team player", "results-driven", "proven track record",
"great fit", "perfect fit", "touch base", "circle back", "talented", "skilled",
"seasoned", "motivated", "driven", "My name is",
"I'd like to share", "I'm confident", "I believe my", "contribute to your team",
"the opportunity to", "This experience taught me", "commitment to", "efforts to",
"impressed by", "intrigued by", "my skills", "well-positioned", "value I can bring",
"make an impact", "I look forward to".

THE OPENER - the first sentence decides whether they read the second:
- Never open with who you are or what you'd like. Open with THEM: their hiring need,
  their product, their technical problem (from the verified context) - or the collision
  between their problem and your result.
- Shapes that work (adapt with the REAL facts, never copy the wording):
    "You're hiring backend engineers for the payments rebuild. I shipped exactly that: <result with number>."
    "<Their hard problem from the context> is what I spent the last year on: <result with number>."
- Instant delete: "I'd like to...", "I'm a backend engineer with...", "My experience in...",
  "I recently came across your company...".

WHAT MAKES IT LAND:
- Numbers do the bragging: "cut p95 latency 38%" beats any adjective. Two real numbers
  from the candidate's background, maximum.
- One small natural aside is fine ("took three rewrites to get there"). Light
  imperfection reads as human; polish reads as generated.
- Subject line: 3-6 plain words in lowercase, like a quick internal note ("scaling rag
  pipelines", "your backend role - quick q"). Never Title Case Every Word, never a
  formal title ("Application for Software Engineer"), and never a headline about the
  candidate's own project - the subject is about THEM or the shared problem.
- When a "MOST RELEVANT background" list is given below, anchor BOTH the subject and
  the opening in that shared ground — the specific tech or problem you both touch
  ("llm eval pipelines", "cutting checkout latency") — never a generic greeting.
- End with ONE easy, low-stakes question, under 12 words ("worth a quick chat?").

GIVE THEM A REASON TO REPLY:
- Every sentence must carry a fact: a number, a system name, a decision made. If a
  sentence could appear unchanged in anyone else's email, delete it.
- If the context names a concrete technical problem they have, spend ONE sentence on
  a specific idea you'd try for it. One real idea beats any credential - it gives them
  a taste of working with you and makes replying feel useful instead of charitable.
- Micro-offers convert: "want the one-page write-up?", "happy to send the benchmark
  numbers". A yes/no offer is easier to answer than "open to a chat sometime?".

THE ASK - the reader must know exactly what you want:
- Every email makes ONE specific, unmistakable request. Pick whichever fits best:
  (a) a specific open role - name the exact role if the context mentions one,
  (b) a 15-minute chat about their team or the problem they're hiring for,
  (c) being looped into their hiring/TA process for current or upcoming roles.
- The closing question IS that ask. Never end on something vague ("would love to
  connect", "let me know your thoughts") - ask for the role, the chat, or the intro.
- If the recipient or company is marked unknown above, follow that instruction
  literally: no names, no placeholders, no "[Company]".

FORMAT - write the BODY only:
- Do NOT write any greeting ("Hi", "Hey", "Hello", "Dear ...") - added automatically.
- Do NOT write any sign-off or signature ("Best", "Regards", "Thanks", your name) - added automatically.
- Start directly with the first sentence of the message. End on the CTA line. Nothing before or after.
- No paragraph longer than 2 sentences.

{_NO_FABRICATION}

DON'T ORBIT ONE THING:
- Don't build the whole email around a single detail repeated three ways.
- Use TWO distinct, concrete points from the candidate's background, different in kind
  (one technical win + one ownership/scale moment), picked for THIS recipient's role.
- Vary the opening line - no two emails should start the same way."""


TEMPLATES: dict[str, str] = {

    # ── FORMAL register ───────────────────────────────────────────────────────

    "hiring_inbox": f"""\
You are writing a job application email to a company's hiring inbox
(careers@ / jobs@) on behalf of a candidate. This is a shared team mailbox:
address the team, never an individual, and make it easy to route to the
right recruiter.

Company: {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate: {{sender_name}}
Candidate background:
{{resume}}

{_FORMAL_RULES}

Length: 90-130 words.
Structure:
1. One line: who the candidate is (name + current role) and the role type they
   are applying for - the exact open role from the context if one is named,
   otherwise a sensible role family from their background (e.g. "SDE/backend roles").
2. Two or three lines: their most relevant experience for THIS company - real
   systems and real numbers, drawn from the MOST RELEVANT list when present.
3. One line: why this company (only from verified context; with no context, one
   plain honest line about wanting to work on their kind of problems).
4. Close: ask to be considered for relevant current or upcoming openings, and
   thank them for their time.{{attachment_note}}

Subject line: "<Role> Application - <Candidate Name>" using the real role and
the candidate's real name (e.g. "SDE Application - Priya Nair"). If the
candidate's name is unknown, just "<Role> Application".

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only - no greeting, no sign-off, no signature]
""",

    "recruiter": f"""\
You are writing a professional email on behalf of a job seeker to a named
recruiter / talent-acquisition person, asking about open roles.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate: {{sender_name}}
Candidate background:
{{resume}}

{_FORMAL_RULES}

Length: 80-110 words. Recruiters scan; front-load the essentials.
Structure:
1. One line: asking about open roles that fit the candidate - name the exact
   role from the context if one is present.
2. Two lines: current role plus the most relevant skills/systems with real
   numbers, drawn from the MOST RELEVANT list when present.
3. Close: ask them to review the profile and share any suitable opening.{{attachment_note}}

Subject line: "<Role> Opportunity - <Candidate Name>" (e.g. "SDE Opportunity -
Priya Nair"). If the candidate's name is unknown, "<Role> opportunity inquiry".

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only - no greeting, no sign-off, no signature]
""",

    # ── DIRECT register ───────────────────────────────────────────────────────

    "engineering_leader": f"""\
You are writing a cold email from a software engineer to a senior technical leader
(CTO, VP Engineering, Engineering Manager, Tech Lead, Principal Engineer, Staff Engineer).
This person has hiring authority and cares deeply about technical quality.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 110–140 words.
What this person cares about: systems you've owned, what broke and how you fixed it,
architectural decisions, scale, specific tech choices and WHY you made them.
If company context mentions their stack or technical challenges — tie directly to that.
Do NOT list skills like a résumé. Tell ONE concrete thing you built, the scale/impact, and a
hint at the problem-solving process behind it. Make them think "this person gets it."

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only — no greeting, no sign-off, no signature]
""",

    "peer_engineer": f"""\
You are writing a cold email from a job seeker to a PEER software engineer — not to a hiring manager.
This person CANNOT make a hiring decision. They CAN refer you to their team or recruiter,
or give you an honest take on what it's like to work there.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 55–75 words MAXIMUM. Tight. Ruthlessly cut anything that doesn't earn its place.

CRITICAL — the ask is a REFERRAL or INSIDER INFO, not a hiring pitch:
- Do NOT write "I'd love to join your team" (they can't decide that).
- DO write something like "would you be open to a 15-min chat about what it's like there?"
  or "any chance you could pass my name to your recruiter?"

Structure (one sentence each):
1. Specific callout of their actual work (from context — never invent this)
2. Your single strongest credential with a concrete number or outcome
3. The ask — referral intro, a quick call, or pass your info to their recruiting team

Subject: 3–5 words, casual. "referral at {{company}}?" / "quick ? about {{company}}" /
"[their-tech] at {{company}}" — pick the most natural fit.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only — no greeting, no sign-off, no signature]
""",

    "founder": f"""\
You are writing a cold email from a job seeker to a startup founder or CEO.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 60–85 words MAX. Founders are perpetually busy. Every single word must earn its place.
Open with something specific about what {{company}} is building or the problem they're solving —
NOT generic praise ("I love what you're building" is useless noise to a founder).
One sentence on why you specifically are relevant right now.
One soft ask that respects their time — best shapes: consideration for an
engineering role, or "could you point me to the right person on your team?".
Show you've done your homework without being sycophantic.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only — no greeting, no sign-off, no signature]
""",

    "product": f"""\
You are writing a cold email from a candidate to a Product Manager or Head of Product.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 90–120 words.
PMs care about: shipping, user impact, cross-functional ownership, prioritisation under constraints,
and saying no to good ideas in favour of better ones.
Lead with something specific about {{company}}'s product if context allows — a design decision,
a feature direction, a market positioning choice.
Show that you ship things and work fluidly with engineers and designers — not just write specs.
Avoid the word "product" more than once.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only — no greeting, no sign-off, no signature]
""",

    "business_leader": f"""\
You are writing a cold email to a business-side leader (VP Sales, Head of Marketing, COO, Director, GM).

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 90–110 words.
This person cares about: revenue, growth, efficiency, cross-functional execution, hitting targets.
Avoid technical jargon — translate any technical wins into business outcomes (revenue, retention,
efficiency gains, cost savings). Use real numbers.
Reference {{company}}'s business context if available — market position, growth stage, a recent move.
Open with something that signals you understand their world, not just your own background.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only — no greeting, no sign-off, no signature]
""",

    "followup": f"""\
You are writing a short follow-up email. The first email got no reply.

Original email sent to {{name}} at {{company}}:
{{original_email}}
{{context_block}}
{_TONE_RULES}

Length: 2–3 sentences ONLY. That is the hard ceiling.
Do NOT re-pitch yourself. Do NOT apologise for following up.
Add ONE small new angle — a different reason to respond, a fresh hook, a new question.
If verified context is provided above, draw the new hook from a detail the original
email didn't use. Something genuinely different, not a rephrasing.
Tone: light, low-pressure, slightly self-aware. Make it feel human, not like a drip campaign.
End with a very easy yes/no question or a one-sentence soft close.

Return EXACTLY this format (no extra text):
SUBJECT: Re: [original subject line]

BODY:
[follow-up message body only — no greeting, no sign-off, no signature]
""",
}


# Templates in the FORMAL register — the generator skips casual-voice
# scrubbing/scoring for these (fabrication checks still apply).
FORMAL_KEYS = frozenset({"hiring_inbox", "recruiter"})

# Acceptable body length per template (words), used by the deterministic
# quality scorer. Slightly wider than the prose targets in the templates so a
# draft a few words outside the ideal isn't needlessly regenerated.
WORD_RANGES: dict[str, tuple[int, int]] = {
    "hiring_inbox":       (70, 140),
    "recruiter":          (55, 115),
    "engineering_leader": (75, 145),
    "peer_engineer":      (35, 85),
    "founder":            (40, 95),
    "product":            (65, 125),
    "business_leader":    (65, 120),
    "followup":           (15, 70),
}


def get_designation_key(designation: str) -> str:
    """Map a designation string to the best email template."""
    d = designation.lower()

    # Shared hiring inboxes (careers@/jobs@ role-inbox leads) get the formal
    # application template — checked first since the designation also contains
    # "talent"/"recruiting", which would otherwise match the recruiter branch.
    if "role inbox" in d:
        return "hiring_inbox"

    # C-suite founders always get the founder template
    if any(x in d for x in ("founder", "ceo", "coo", "co-founder", "owner",
                             "managing director", "md", "chief executive")):
        return "founder"

    # Senior engineering leaders with hiring authority
    if any(x in d for x in ("cto", "vp eng", "vp of eng", "head of eng", "head of engineering",
                             "engineering manager", "em,", " em ", "tech lead", "technical lead",
                             "principal engineer", "staff engineer", "distinguished engineer",
                             "director of eng", "engineering director", "software director",
                             "vp engineering", "chief technology", "chief architect")):
        return "engineering_leader"

    # Peer engineers — no hiring authority; write a referral-ask, not a pitch
    if any(x in d for x in ("engineer", "developer", "swe", "software engineer",
                             "backend", "frontend", "fullstack", "full stack",
                             "devops", "sre", "platform engineer", "infrastructure",
                             "data scientist", "ml engineer", "data engineer",
                             "mobile engineer", "android", "ios engineer")):
        return "peer_engineer"

    # Product roles
    if any(x in d for x in ("product manager", "pm,", " pm ", "head of product", "vp product",
                             "director of product", "chief product", "cpo", "product lead")):
        return "product"

    # Recruiting / HR — the default for ATS-sourced contacts
    if any(x in d for x in ("recruiter", "recruiting", "talent acquisition", "ta,", " ta ",
                             "hr ", "human resource", "people ops", "people partner",
                             "hiring manager", "talent partner")):
        return "recruiter"

    # Business leaders
    if any(x in d for x in ("vp", "director", "head of", "chief", "cmo", "cfo",
                             "sales", "marketing", "growth", "operations",
                             "general manager", " gm", "business development")):
        return "business_leader"

    # Default: recruiter template is the safest for unknown roles
    return "recruiter"
