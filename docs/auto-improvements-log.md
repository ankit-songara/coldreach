# Auto-Improvements Log

Nightly autonomous improvement passes on ColdReach. Each entry is one run.

## 2026-07-18

**Looked at:** backend/app/api/{hunt,compose,send}.py, backend/app/llm/{generator,quality,relevance}.py,
backend/app/scrapers/*, frontend hooks and Compose/Send/Hunt components — via an Explore pass grounded in
the actual current code (no duplicate proposals against already-shipped work: scraper junk filtering,
email-pattern memory, draft quality pipeline, résumé relevance matching, keep-alive tabs, shared query
hooks, auto-reply-check, search/role filters).

**Changed:**
- [backend/app/api/send.py](../backend/app/api/send.py) — dedupe `req.contact_ids` before resolving
  contacts in `bulk_send`. A repeated id in the request (client retry, double-click) built duplicate
  `(contact, draft)` queue entries, so `smtp.sendmail` could fire twice for the same recipient in a single
  batch. Fixed with `dict.fromkeys()` to preserve order while removing dupes.
- [backend/app/llm/generator.py](../backend/app/llm/generator.py) — `_first_name` now splits on
  `[^A-Za-z'\-]` instead of `[^A-Za-z]`, so names like `"D'Angelo"` or `"Anne-Marie"` keep their apostrophe/
  hyphen instead of being truncated to `"D"` / `"Anne"`. Matches the `_NAME_TOKEN_RE` pattern already used
  in `scrapers/base.py` for consistency.

Both verified: `pytest tests/test_api.py -q` → 143 passed; `npx tsc --noEmit` → clean.

**Bigger ideas noticed, not implemented (need human/product judgment):**
- `backend/app/llm/quality.py:_grounded` uses a 5-char prefix substring match against the raw context
  string (not word-boundary aware), so a claim word's prefix can spuriously match inside an unrelated
  longer context word (e.g. `"scale"` matching `"prescale"`). Tightening this touches the hand-tuned
  fabrication-scrubber precision/recall balance from recent work — risky to change without evaluating
  against real draft samples, so left alone.
- `backend/app/api/hunt.py:_find_live_domain` strips only the last DNS label when building alt-TLD
  candidates, so multi-part TLDs (e.g. `acme.co.uk`) produce wrong guesses like `acme.co.io`. Real but
  low-frequency; deferred because a correct fix needs a public-suffix-list-aware split, which is more than
  a small patch.
- `verify.py`'s Hunter-backed verification doesn't accept a per-request `hunter_api_key` override, unlike
  `hunt.py` which does — looks like an intentional scope difference, not a bug, but worth a product
  decision on whether `/verify` should support the override too.
- Hunt's `_infer_role_from_query` filter is binary (apply filter or not); extending it to multi-family
  scoring is a product behavior call, not a small fix.

## 2026-07-18 (second pass)

**Looked at:** a broad re-scan of files not covered by the earlier pass today — backend/app/api/{inbox,
automation,contacts,companies,resume}.py, backend/app/mailer.py, backend/app/timeutil.py,
backend/app/db/crud.py — grounded in the actual current code, cross-checked against both the "already
shipped" list and the "changed today" list above so nothing here duplicates prior work.

**Changed:**
- [backend/app/api/resume.py](../backend/app/api/resume.py) — `_clean()`'s de-spacing step never actually
  worked. The first regex used a backreference (`\1`), so it only collapsed runs of the *same* letter
  repeated (e.g. `"A A A"` → `"AAA"`) — it could never fix the docstring's own example,
  `"E x p e r i e n c e"` → `"Experience"`, because those are different letters. A second regex meant to
  handle the general case turned out to be a no-op (verified: identical string in and out for that exact
  input). Replaced both with one regex that collapses any run of 3+ single-letter tokens separated by
  single spaces — this is the actual signature of PDF font-kerning extraction artifacts. Verified against
  the docstring example, a "W O R K" style header, plain sentences (`"I am a backend engineer..."`), and an
  acronym case (`"U S A based"` → `"USA based"`) to confirm no false positives on normal résumé text.

Verified: `pytest tests/test_api.py -q` → 143 passed; `npx tsc --noEmit` → clean.

**Bigger ideas noticed, not implemented (need human/product judgment):**
- None new this pass beyond what's already logged above — the rest of the newly-reviewed files
  (inbox.py's IMAP reply/bounce detection, automation.py's Gmail config flow, contacts.py CRUD,
  companies.py, mailer.py, timeutil.py, crud.py) looked correct on inspection; no additional low-risk fix
  surfaced without speculating.
