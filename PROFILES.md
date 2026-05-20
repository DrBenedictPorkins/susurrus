# Creating a Susurrus Profile

A **profile** controls what Susurrus does with the live transcript: who the advisor is, what tone it takes, what backstory it operates from, and what specialized terms it tries to define. Each profile is **one Python file + three prompt files** under `local/`.

This guide walks you through creating one from scratch. You can fill in the templates by hand, or hand the LLM-prompts in each step to Claude / GPT / Gemini and let it draft for you.

---

## What you're building

| File | Purpose |
|---|---|
| `local/profiles/<name>.py` | Wires the pipeline steps together for your profile |
| `local/prompts/<name>_advisor.md` | **The suggestor / advisor prompt** — the persona, output format, tone, watch-for list |
| `local/prompts/<name>_backstory.md` | Free-form context (participants, project, in-house jargon). Editable live from the UI. |
| `local/prompts/<name>_terms.md` | **The term searcher prompt** — what counts as an "interesting term" worth looking up |

Files in `local/` are gitignored and take precedence over the public showcase versions in `profiles/` and `prompts/`.

---

## Step 1 — Decide what you're making

Before writing anything, jot down answers:

- **Profile name** — kebab-case, e.g. `standup`, `client-pitch`, `legal-review`, `dm-prep`. The prompt prefix is usually the same but snake_case.
- **What kind of call is this?** (recurring team standup / one-off pitch / open-ended discussion / DM session)
- **What's the advisor's job?** (summarize, raise flags, prep me for what's coming, fact-check, surface decisions being made implicitly)
- **What tone fits?** (neutral / curious / blunt / cautious / playful)
- **What kinds of terms should be defined?** (legal jargon, financial mechanics, infra acronyms, in-house product names, named people/places)

Hold onto these. You'll feed them into the LLM prompts below.

---

## Step 2 — Copy the demo

```bash
cd /path/to/susurrus
PROFILE=standup     # ← your kebab-case profile name
PFX=standup         # ← snake_case prompt prefix (usually the same word)

cp profiles/demo-meeting.py            local/profiles/$PROFILE.py
cp prompts/demo_meeting_advisor.md     local/prompts/${PFX}_advisor.md
cp prompts/demo_meeting_backstory.md   local/prompts/${PFX}_backstory.md
cp prompts/demo_meeting_terms.md       local/prompts/${PFX}_terms.md
```

---

## Step 3 — The advisor prompt (`<prefix>_advisor.md`)

The most important file. It's the system prompt for Claude every time the advisor speaks (after every ~3 finalized paragraphs). It's *also* the persona used for Q&A when you press ASK.

### What the model sees on each AUTO call

Each call sends Anthropic four things, in this order:

1. **System prompt** = your `<prefix>_advisor.md` file (re-read fresh), with any active INSTRUCT appended as `Active instruction from user: ...`.
2. **`[Backstory]` user-message block** = your `<prefix>_backstory.md` file (re-read fresh). Marked cacheable.
3. **`[Transcript]` user-message block** = the *entire finalized transcript so far*, plus the in-progress paragraph (the one currently being transcribed). Marked cacheable.
4. **`[Current]` user-message block** = the most recent ~3 finalized paragraphs that just triggered this call. NOT cached — this is the only part that changes per call.

Implications for how you write the prompt:

- The advisor always has the **full conversation** in scope — don't write rules that assume a sliding window or "recent" context. Tell it to react to `[Current]` but use `[Transcript]` for memory ("don't repeat a question already answered earlier").
- The backstory and transcript hit Anthropic's 5-minute prompt cache, so each call effectively pays only for the `[Current]` delta. Long backstories aren't free — they bloat the cache and add noise — but they're cheap on the wire.
- The session persists across restarts (paragraphs are saved to `session_<profile>.json`), so the `[Transcript]` block can be long across multi-session usage.

### What to define in the prompt
- **Persona** — "You are a silent co-pilot during a {{type-of-call}}..."
- **Audience** — Who reads the output? Just you, or shared?
- **Output format** — What does each suggestion look like? Fields, lines, max length.
- **Hard rules** — What MUST it never do? (no markdown, no speaker-name guessing, no repeated questions)
- **Tone** — Default voice; how it should adapt when the user sends an INSTRUCT.
- **Watch-for list** — Priority-ordered list of what's worth flagging in this kind of call.
- **Examples** — 2–3 ideal outputs (models lean heavily on these).

### Hand this to an LLM to draft it for you

> Help me write `local/prompts/{{prefix}}_advisor.md` for the **Susurrus** project. Susurrus is a real-time transcription + LLM co-pilot for live calls; the advisor reads the running transcript and emits short commentary every few paragraphs into a "LIVE NOTES" panel that only I see.
>
> On each AUTO call the advisor receives, in this order: (1) my advisor-prompt as the system message (with any active INSTRUCT appended); (2) a cached `[Backstory]` block; (3) a cached `[Transcript]` block containing the entire finalized transcript so far plus the in-progress paragraph; (4) a non-cached `[Current]` block with the most recent ~3 finalized paragraphs that triggered this call. So the prompt should tell the advisor to react to `[Current]` while using `[Transcript]` as memory (e.g. for not repeating questions already answered).
>
> Context for this profile:
> - **Profile name:** {{e.g. "standup"}}
> - **Call type:** {{e.g. "daily engineering standup, ~10 people, 15 min"}}
> - **Advisor's job:** {{e.g. "surface implicit blockers, optimistic estimates, side-channel decisions, and missing context for new joiners"}}
> - **Tone:** {{e.g. "curious, not pushy"}}
> - **What I want to watch for, in priority order:**
>   1. {{e.g. unstated blockers}}
>   2. {{e.g. estimates that sound optimistic}}
>   3. {{e.g. side-channel decisions}}
>   4. {{...}}
>
> Universal hard constraints (apply to every profile):
> - No markdown in output (no `**bold**`, headers, lists)
> - No speaker-name attribution — Whisper doesn't separate speakers, the transcript is one stream. Default to neutral phrasing ("the team discussed..." not "Alice said...") unless the transcript itself contains an explicit attribution like "Alice: ...".
> - Never repeat a question that's already been answered in the transcript.
> - Never re-define a term already defined earlier.
>
> Default output format:
>
> ```
> SUMMARY: <one or two sentences in plain English. No markdown.>
> FOLLOWUP: <one specific clarifying question or note worth raising. No markdown.>
> ```
>
> For chatter (greetings, mic checks, scheduling, side jokes), output only `OOC: <short phrase>`.
>
> Match the style and structure of `prompts/demo_meeting_advisor.md`: persona → output format → hard rules → tone → watch-for list → 2–3 example outputs. End with at least two right-voice examples and at least one wrong-voice example labeled "do NOT do".

---

## Step 4 — The backstory (`<prefix>_backstory.md`)

Free-form context that's prepended to every Anthropic call. **Editable live from the UI** — click the profile name in the TRANSCRIPT panel header.

Keep it short (50–200 words). Long backstories blow your prompt cache and add noise. Stable stuff here (who's on the team, what the project is, what acronyms are house-specific); *call-specific* stuff goes into the live INSTRUCT input instead.

### Template

```markdown
# {{Profile}} Backstory

## The setting
{{One or two lines about who's on the call and what kind of session it is.}}

## What matters
{{3-5 bullets of stable context — recurring participants, project names, in-house terminology, anything that always matters in this kind of call.}}

## What to watch for
{{Optional: anything you reliably want flagged in this kind of call.}}
```

### Hand this to an LLM to draft it for you

> Help me write `local/prompts/{{prefix}}_backstory.md` for the Susurrus project. This file gets prepended to every Anthropic call for the advisor. ~100–200 words of stable context.
>
> Context:
> - **Call type:** {{e.g. "engineering team standup"}}
> - **Recurring participants:** {{names + roles, e.g. "5 backend engineers, 2 frontend, 1 PM"}}
> - **Recurring topics:** {{e.g. "infra migration, customer escalations, weekly velocity"}}
> - **In-house terminology and acronyms:** {{e.g. "Edge = our CDN tier; Brain = our orchestration service; the migration = the move off Heroku"}}
> - **What "good" looks like in this meeting:** {{e.g. "blockers surfaced with named owners, no over-reporting, decisions explicit not implicit"}}
>
> Keep it short, under 200 words. Stable facts only — anything call-specific I'll add via the live INSTRUCT input.

---

## Step 5 — The term extractor (`<prefix>_terms.md`)

Drives the **term lookup** feature. When enabled (toggle in the FORMATTED panel header), every finalized paragraph is scanned for "interesting terms" worth defining; each one gets a 1–3 sentence definition with web sources.

This file controls **what counts as interesting for your domain**. The generic `demo_meeting_terms.md` is broad; tailoring this is where you get good signal.

### What the model sees on each extraction call

One call per finalized paragraph:

1. **System prompt** = your `<prefix>_terms.md` file + the shared `prompts/term_extractor_format.md` (the JSON-output contract — appended automatically, don't include it).
2. **`[Backstory]` block** = your `<prefix>_backstory.md` (re-read fresh, cached).
3. **User message** = the running list of already-known terms ("do NOT extract these again") + the new paragraph text.

Once the extractor returns a term, a *separate* Anthropic call defines it (using `prompts/term_lookup.md` as system + the `web_search` tool). You don't author the lookup prompt — just edit `prompts/term_lookup.md` directly if you want different definition style.

### Template

```markdown
You scan a single newly-finalized paragraph from a {{kind of call}} and pick out NEW terms a non-expert listener would want defined.

Focus categories (priority order):

1. **{{Category 1, e.g. "Legal jargon"}}**: {{specific examples, e.g. "NDA, indemnification, joint and several liability, force majeure, MFN clause"}}
2. **{{Category 2}}**: {{...}}
3. **{{Category 3}}**: {{...}}

DO NOT pick:
- Common English words
- Vague references ("the thing", "they", "the contract")
- Anything already in the known list (case-insensitive)
- Terms you can't be sure are real, well-defined things

Be selective. Five high-signal terms beat twenty weak ones. If nothing new and interesting appears in this paragraph, return an empty list.
```

### Hand this to an LLM to draft it for you

> Help me write `local/prompts/{{prefix}}_terms.md` for Susurrus. This prompt is given to Claude once per finalized paragraph (along with my cached backstory and a running list of already-known terms); Claude returns a JSON list of NEW "interesting terms" that then get individually looked up in a separate call.
>
> Context:
> - **Domain:** {{e.g. "venture-stage angel investment discussions"}}
> - **What I want defined:** {{e.g. "deal mechanics (SAFE, SPV, K-1, pro-rata), financial metrics (MRR, ARR, burn, runway), round mechanics (seed, A/B/C, bridge, convertible note), named entities (companies, funds, people in roles), regulatory terms"}}
> - **What I do NOT want defined:** {{e.g. "common English words, vague references, code names, ordinary business terms anyone would know"}}
> - **Tolerance for false positives:** {{e.g. "low — I'd rather miss a term than get noise"}}
>
> Format: free-form prose with numbered focus categories. The JSON-output contract is appended automatically — do not include it. Match the style of `prompts/demo_meeting_terms.md`.

---

## Step 6 — Update the profile `.py`

Open `local/profiles/standup.py` and update three things:

```python
_advisor = AdvisorStep(
    prompt_name="standup_advisor",          # ← your prefix + "_advisor"
    backstory_name="standup_backstory",     # ← your prefix + "_backstory"
    doc=_doc,
    session_file="session_standup.json",    # ← rename
    log_tag="standup",                      # ← rename (shows up in stderr logs)
)
...
_term_lookup = TermLookupStep(
    extractor_prompt_name="standup_terms",  # ← your prefix + "_terms"
    backstory_name="standup_backstory",
    doc=_doc,
)
```

Save. No other changes needed.

---

## Step 7 — Launch and iterate

```bash
./local/run.sh standup
# or:
uv run python transcribe.py standup
```

Startup should print:

```
[profile] loaded: standup (local)
```

Then:

1. Open `http://localhost:8765`
2. Start sending audio (Audio Hijack on port 8000, or `./capture.sh` in `"tcp"` mode)
3. Watch the LIVE NOTES panel — the first AUTO suggestion fires after 3 finalized paragraphs (~30–60 sec of speech)
4. Enable term lookup via the toggle in the FORMATTED panel header

**Prompts re-read on every call**, so you can iterate live:
- Edit `local/prompts/standup_advisor.md` in your editor → next AUTO suggestion uses it.
- Click the profile name in the UI → edit the backstory in a textarea → Save → next call sees the change.
- Type into the INSTRUCT input at the bottom of LIVE NOTES → next AUTO call adopts it. Use this for *moment-specific* tweaks ("today focus on velocity issues") that don't belong in the backstory.

---

## Tips that save time

- **Start broad, tighten over time.** Your first run will be wrong. Watch a few real suggestions, *then* edit the rules.
- **Examples beat rules.** A minute on one good example saves ten minutes of rule-tweaking. Aim for 2–3 ideal-output examples per profile.
- **Backstory = stable. INSTRUCT = today.** Don't pollute the backstory file with "today's agenda" — use INSTRUCT for that.
- **If term picks are noisy, fix `_terms.md` only.** The advisor and term lookup are independent — bad term picks don't degrade commentary.
- **The advisor prompt also drives Q&A.** When you press ASK, the same prompt is the system prompt (with a small Q&A-specific addendum the project appends automatically). So the persona stays consistent.
