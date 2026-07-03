"""
Cold email prompt templates, keyed by designation type.
Add new templates here without touching generator.py.
"""

# Shared tone rules injected into every template.
# Built around the known tells of machine-written email (em dashes, rule-of-three
# lists, "-ing" tack-ons, negative parallelisms, stock phrases): recipients who
# read hundreds of emails pattern-match these instantly, and the email dies.
_TONE_RULES = """\
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
"seasoned", "motivated", "driven", "My name is".

WHAT MAKES IT LAND:
- Open with ONE specific, true observation about the recipient or company (only from
  the verified context below), or with the candidate's single most relevant concrete fact.
- Numbers do the bragging: "cut p95 latency 38%" beats any adjective. Two real numbers
  from the candidate's background, maximum.
- One small natural aside is fine ("took three rewrites to get there"). Light
  imperfection reads as human; polish reads as generated.
- Subject line: 3-6 plain words, like a colleague's internal email ("quick question
  about your data team"), never a formal title ("Application for Software Engineer").
- End with ONE easy, low-stakes question, under 12 words ("worth a quick chat?").

FORMAT - write the BODY only:
- Do NOT write any greeting ("Hi", "Hey", "Hello", "Dear ...") - added automatically.
- Do NOT write any sign-off or signature ("Best", "Regards", "Thanks", your name) - added automatically.
- Start directly with the first sentence of the message. End on the CTA line. Nothing before or after.
- No paragraph longer than 2 sentences.

DO NOT FABRICATE - this is the most important rule:
- NEVER invent facts about the company: no made-up product names, funding rounds, metrics,
  tech stack, team size, or "I saw you shipped X" unless X appears in the context below.
- If you have no real detail about them, say something honest and direct instead.
- A generic-but-true line beats an impressive-but-invented one. Recipients can tell instantly.

DON'T ORBIT ONE THING:
- Don't build the whole email around a single detail repeated three ways.
- Use TWO distinct, concrete points from the candidate's background, different in kind
  (one technical win + one ownership/scale moment), picked for THIS recipient's role.
- Vary the opening line - no two emails should start the same way."""


TEMPLATES: dict[str, str] = {

    "recruiter": f"""\
You are writing a cold email on behalf of a job seeker to a recruiter or talent acquisition person.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 80–110 words. Recruiters see hundreds of emails — get to the point immediately.
Structure: 1 line hook (tie strongest skill to their hiring context) → 2 lines of most relevant
experience with real numbers → 1 soft CTA question.
Do NOT open with "I'm a [title]" — that's what every email starts with. Lead with value.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[message body only — no greeting, no sign-off, no signature]
""",

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
One soft ask that respects their time.
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


def get_designation_key(designation: str) -> str:
    """Map a designation string to the best email template."""
    d = designation.lower()

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
