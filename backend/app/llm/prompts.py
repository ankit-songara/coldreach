"""
Cold email prompt templates, keyed by designation type.
Add new templates here without touching generator.py.
"""

# Shared tone rules injected into every template
_TONE_RULES = """\
TONE — read this carefully:
- Write like a real person texting a professional contact, not like a cover letter
- Use contractions (I've, I'm, you're, we've)
- Short sentences. Vary rhythm. No paragraph longer than 2 sentences.
- NEVER use: "Hope this finds you well", "I am writing to express", "I came across",
  "passionate about", "excited to", "leverage", "synergy", "team player",
  "fast-paced environment", "results-driven", "I believe I would be a great fit"
- No adjectives that flatter yourself (talented, skilled, experienced, motivated)
- If company context is given, open with ONE specific observation about that company
  (a product they shipped, a problem space they're in, something concrete — not generic praise)
- Numbers beat adjectives: "reduced latency by 40%" beats "improved performance significantly"
- Subject line: 4–6 words, conversational, NOT descriptive (e.g. "quick question" or a specific hook)
- End with a soft single-line CTA — a question, not a request ("worth a quick chat?" not "I would appreciate...")

DO NOT FABRICATE — this is the most important rule:
- NEVER invent facts about the company: no made-up product names, funding, metrics,
  tech stack, or "I saw you shipped X" unless X appears in the context below.
- If you have no real detail about them, say something honest instead of a fake specific.
- A generic-but-true line beats an impressive-but-invented one. Recipients can tell.

DON'T ORBIT ONE THING:
- Don't build the whole email around a single detail repeated three ways.
- Use TWO distinct, concrete points from the candidate's background — pick the two most
  relevant to THIS recipient's role, and make them different in kind (e.g. one technical
  win + one collaboration/ownership moment), not two versions of the same achievement.
- Vary the opening line by recipient — no two emails should start the same way."""

TEMPLATES: dict[str, str] = {

    "recruiter": f"""\
You are writing a cold email on behalf of a job seeker to a recruiter or talent acquisition person.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 80–110 words. Recruiters see hundreds of emails — get to the point fast.
Structure: 1 line hook → 2 lines of most relevant experience with numbers → 1 line CTA
The hook should tie the candidate's strongest skill directly to {{company}}'s hiring context if available.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[email — no greeting like "Dear", start with the person's first name or jump straight in]
""",

    "engineering_leader": f"""\
You are writing a cold email from a software engineer to a technical leader (CTO, VP Eng, Engineering Manager, Tech Lead).

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 110–140 words.
This person cares about: what systems you've built, what broke and how you fixed it, scale, specific tech choices.
If company context mentions their stack or technical challenges — tie directly to that.
Do NOT list skills like a résumé. Tell one concrete thing you built and why it mattered.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[email — address them by first name]
""",

    "founder": f"""\
You are writing a cold email from a job seeker to a startup founder or CEO.

Recipient: {{name}}, {{designation}} at {{company}}
How you found them: {{source_hint}}
{{context_block}}
Candidate background:
{{resume}}

{_TONE_RULES}

Length: 60–85 words MAX. Founders are perpetually busy. Every word must earn its place.
Open with something specific about what {{company}} is building or the problem they're solving.
One sentence on why you specifically are relevant. One soft ask.
No fluff. No "I admire what you're building." Show you understand their world.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[email — address them by first name]
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
PMs care about: shipping, user impact, cross-functional work, prioritisation under constraints.
Lead with something about {{company}}'s product — a feature, a design decision, a market move.
Show that you ship things and work with engineers and designers, not just write specs.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[email — address them by first name]
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
This person cares about: revenue, growth, efficiency, people management, hitting targets.
Avoid technical jargon. Focus on business outcomes you've driven with numbers.
Reference {{company}}'s business context if available — market, growth stage, a recent move.

Return EXACTLY this format (no extra text):
SUBJECT: [subject]

BODY:
[email — address them by first name]
""",

    "followup": f"""\
You are writing a short follow-up email. The first email got no reply.

Original email sent to {{name}} at {{company}}:
{{original_email}}

{_TONE_RULES}

Length: 2–3 sentences ONLY. That's it.
Do not re-pitch. Do not apologise for following up.
Add one small new hook or observation — something different from the original email.
Make it feel like a genuine "just checking" from a real person, not a drip sequence.
Tone: light, no pressure, a little self-aware that you're following up.

Return EXACTLY this format (no extra text):
SUBJECT: Re: [original subject line]

BODY:
[follow-up only — no greeting needed, just the message]
""",
}


def get_designation_key(designation: str) -> str:
    """Map a designation string to the best template key."""
    d = designation.lower()

    if any(x in d for x in ("founder", "ceo", "coo", "co-founder", "owner", "managing director", "md")):
        return "founder"

    if any(x in d for x in ("cto", "vp eng", "vp of eng", "head of eng", "engineering manager",
                             "em ", "tech lead", "principal engineer", "staff engineer",
                             "director of eng", "software director")):
        return "engineering_leader"

    if any(x in d for x in ("product manager", "pm ", "head of product", "vp product",
                             "director of product", "chief product", "cpo")):
        return "product"

    if any(x in d for x in ("ta", "talent", "recruiter", "hr ", "human resource",
                             "people ops", "people partner", "recruitment")):
        return "recruiter"

    if any(x in d for x in ("vp", "director", "head of", "chief", "cmo", "cfo",
                             "sales", "marketing", "growth", "operations", "general manager", "gm")):
        return "business_leader"

    # Default: most cold emails go to recruiters or generalists
    return "recruiter"
