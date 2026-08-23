# Decisions — why the system works this way

`SPEC.md` holds the architecture and the implementation contract: diagrams, exact rules, exact
column names. This document explains the reasoning, and **wins wherever the two disagree.**

**For a non-technical reader.** Every month the team fills a room with exactly the engineers it wants
to hire, and within days those contacts are gone. This system turns each event into a permanent,
searchable talent pool: every attendee is enriched once and stored, and when a role opens the pool is
ranked against it in seconds. A recruiter sees a short list — the person, a match percentage, the
skills they have and the ones they're missing, and where one exists, the name of the colleague who
already knows them. Every score opens up to show how it was calculated, so the shortlist can be
defended to a hiring manager rather than trusted blindly.

---

## 1. The central decision: two flows, not one

The pipeline is **two flows on two triggers**, not one continuous run.

> **Partition rule.** Anything true about a person regardless of any job → **Flow A**, once per
> person, after each conference. Anything measured against a specific job → **Flow B**, on demand
> when a role opens.

So skill *normalization* is Flow A and skill *overlap* is Flow B; note *tag extraction* is Flow A and
note *scoring* is Flow B; referral *edge construction* is Flow A and referral *selection* is Flow B.

Three consequences make the system work:

- **Expensive work happens once per person, not once per person per job.** Enrichment is rate limited
  and costs per lookup; running it per query would not survive contact with a real provider.
- **Flow B makes no external call of any kind.** It is arithmetic over pre-processed data, so it
  stays fast as the pool grows.
- **The flows share no process state.** The pool on disk is the only interface, so ingestion can run
  today and matching next month.

Diagrams and the step-by-step contract: `SPEC.md` §0, §4, §5.

---

## 2. The seven assumptions

| # | Question | Answer in one line |
| :--- | :---- | :---- |
| 2.1 | Defining domain relevance | A weighted, transparent score — never a filter |
| 2.2 | No LinkedIn profile match | Kept and flagged, scored over the components that exist |
| 2.3 | 1 mutual connection vs. 3 | Neither scores points; both become *who to ask*, graded by tier |
| 2.4 | Candidates already in the ATS | Flagged via `ats_status`, read-only lookup |
| 2.5 | Refresh cadence | Ingest per conference; match on demand; re-enrich every 6–12 months |
| 2.6 | Who triggers it | Ingestion automatic, matching recruiter-initiated, ATS handoff manual |
| 2.7 | Privacy / GDPR | Pool lives outside the ATS, soft opt-in, provenance and deletion mandatory |

### 2.1 How is domain relevance defined?

**Not as a filter — as a weighted, transparent score across seven components.** Nobody is excluded
before being scored.

A DevOps conference draws SREs *and* IT managers *and* vendor reps. The tempting fix is to filter on
title, and it is the wrong one: filtering on title would have removed **Priya Anand** — whose title
contains no "ML" — before her skills were ever read. She is one of the strongest candidates in the
pool for JOB001.

The signal-to-noise problem is real, so it is solved by *ranking*, not exclusion. A vendor rep scores
low on skills, low on title family and low on industry, and sinks. A recruiter sees why.

### 2.2 What happens to a contact with no LinkedIn profile match?

**Kept, not dropped — and visibly flagged.**

Without a profile, exactly three of the seven components are computable: title, field notes and
conference domain. Skills, experience, industry and past companies all come from the profile.
Scoring such a row against the full 100 would penalize *missing data* rather than poor fit, so the
score is normalized over those three weights (25 + 10 + 2 = **37**) and the record carries an
`unverified` flag.

That creates a presentation risk, which the recruiter view handles explicitly: a thin record can
reach a high percentage, so the card carries an **Unverified** badge, the breakdown labels the total
`out of 37, not 100`, and unassessed skills are never rendered as red "missing" chips — the data does
not say the person lacks them, only that nobody checked (`SPEC.md` §7).

Automated name-based lookup is deliberately **not** implemented: common names produce false matches,
and misidentifying a candidate is worse than not identifying one. The registration email remains a
valid outreach path.

In the supplied data all 75 attendees match a profile, so this branch is exercised by
`data/edge_cases/` rather than by the provided files.

### 2.3 Is one mutual connection the same as three?

No — but the answer is **context, not points**.

**Mutual connections carry zero scoring weight.** They say nothing about professional fit; a strong
candidate is strong with zero connections. What the team asked for is *who to ask*, so the output
names the person: "Ask Maya Levi, Senior ML Engineer, AI/ML."

Both sides of the data carry dates — the roster's `work_history`, and the candidate's `past_titles`
(`"Title at Company (YYYY-YYYY)"`). Once both are parsed, a shared former employer stops being a
guess and becomes something checkable: two people who can be shown to have **actually overlapped**,
not merely both drawn a paycheck from the same company at some point.

| Tier | Condition | What the recruiter should do | Pairs |
| :--- | :---- | :---- | ---: |
| **A** | shared employer **+** confirmed overlap **+** a mutual connection | ask first | 12 |
| **B** | no shared employer, 3+ mutual connections | familiarity is plausible | 11 |
| **C** | 1–2 mutual connections, **or** a shared employer backed by only one of {overlap, mutual connection} | worth a careful ask | 60 |
| **D** | shared employer, no overlap, no mutual connection | weakest signal that still earns a row | 2 |

**What the data shows.** Of 75 attendees, 33 have no mutual connections, 37 have one or two, and only
5 reach three — so connection count alone separates almost nobody. Shared-employer matching finds 20
pairs across 12 candidates; 16 have overlapping tenures, 4 do not (one of those is a malformed date
range, treated as "no overlap" rather than guessed at). 32 candidates have no edge at all and are
still ranked, with "No referral path" on screen.

**Two consequences of grading this way.** A shared employer *corroborates* a connection rather than
replacing one, so it only reaches tier A alongside both. And the recruiter-facing string never
overstates: `worked together at Opta Sports, 2019-2021` when the dates confirm it,
`both worked at IDF Intelligence Unit, no overlapping years` when they do not.

Company matching must stay token-bounded. Naive substring matching pairs a candidate from `Intel`
with an employee from `IDF Intelligence Unit` — while a genuine coincidence (two people who really
did both work at IDF Intelligence Unit) must still match.

**Both signals are estimates; the truth comes from asking.** Each edge therefore carries
`referral_feedback`. Two states are implemented — `not_requested` (the default) and `insufficient`
(the colleague does not really know the candidate, so the path is retired). Three more are schema
for the request workflow that does not exist yet: `pending`, `positive`, `reserved`. The
adjustment sits **on top of** the match score rather than inside it, keeping the fit score clean and
making it possible to later measure whether referrals predicted good hires. An `insufficient` reply
retires that path so the same colleague is not asked again.

One employee connects to 14 of the 75 attendees, so a per-employee request cap will be needed — the
number is a team decision.

### 2.4 Should candidates already in the ATS be flagged?

**Yes.** Reaching out to someone rejected two months ago costs credibility. The pipeline emits an
`ats_status` flag, populated in production by a **read-only** lookup returning whether a process
exists, its outcome and its date. Here the column exists and is empty — the field is defined, the
data is not available.

### 2.5 What is the refresh cadence?

Not a one-time batch job.

| Activity | Cadence |
| :---- | :---- |
| Ingestion | after each conference — 2–3 times a month |
| Matching | on demand, when a role opens |
| Profile re-enrichment | every 6–12 months; experience and skills change slowly |
| Archive / delete | after 36 months without interaction |

### 2.6 Who triggers the pipeline?

**Ingestion is automatic** on registration export or event close. **Matching is recruiter-initiated.**
**The ATS handoff is manual by design** — three reasons: consent is the trigger and is not
machine-detectable; a misfiring automation would push hundreds of unqualified leads into a system
explicitly not meant for them; and a small team accepts a button it controls faster than a system
that moves people without asking.

Manual does not mean laborious: one click creates the record, populates known fields, tags the
originating conference and flips pool status. **Tagging the source closes the measurement loop** —
once conference origin is recorded on hires, the team can finally answer which events produce
placements.

### 2.7 Privacy and GDPR

A registrant consented to attend an event, **not** to join a candidate database. Three decisions
follow:

- **The talent pool lives outside the ATS.** The ATS is for people who applied.
- **The registration form carries a soft opt-in** — a low-pressure question about openness to hearing
  about roles. It identifies warm leads and establishes a lawful basis for later outreach.
- **Retention, provenance and deletion are mandatory at scale.** Each record stores its source event
  and capture date, and honours deletion requests.

LinkedIn's terms prohibit scraping, so production enrichment goes through a licensed provider.

---

## 3. Scoring

### 3.1 Methodology

Every component produces a value between 0 and 1; each is multiplied by its weight and summed. The
weight is the ceiling that component can contribute at a perfect match.

| Component | Weight | The 0–1 value |
| :---- | ----: | :---- |
| Skill overlap | 30 | matched required skills ÷ total required |
| Title match | 25 | discipline head-noun of the role, plus a seniority bonus |
| Years of experience | 15 | 1.0 at or above the seniority threshold; scaled below |
| Industry | 13 | 1.0 sports; 0.5 adjacent video/broadcast/streaming; 0 otherwise |
| Field notes | 10 | 1.0 if the note references a key domain; 0.5 if generic; 0 if absent |
| Past companies | 5 | 1.0 sports; 0.5 media or video; 0 otherwise |
| Conference domain | 2 | 1.0 if the event domain overlaps the role's domains; 0 otherwise |

Exact rules for all seven: `SPEC.md` §5.

**Why components return 0–1.** It separates measurement from policy. The component states a fact —
four of five required skills is `0.80`. The weight states a judgment — what that fact is worth for
this role. Keeping them apart is what lets the sliders re-rank instantly, makes the breakdown
readable as `value × weight = points`, and makes normalization for missing data possible at all.

**Why these weights.** Honestly: a reasoned starting point, not a derivation. Deriving them would
require hiring-outcome history showing which signals actually predicted a successful hire. What they
encode is a stated ordering — skills and title dominate because they describe what a person *does*,
industry helps but doesn't decide, the conference barely matters. Two consequences: the weights are
**exposed as sliders**, because whether experience is negotiable for a given role is a recruiting
judgment rather than an engineering one; and once outcomes accumulate they can be calibrated from
results instead of intuition.

### 3.2 What the data showed

Three cases drove three decisions.

**Skill comparison must operate on meaning, not strings.** Priya Anand lists OpenCV and YOLO but not
"Computer Vision" or "Object Detection". Exact matching scores her 2 of 5; meaning-based matching
scores her 4 of 5 — worth roughly 12 points, and the difference between mid-pack and top-tier.

**Conference domain is a weak signal.** Chiara Russo attended a broadcast expo, not a sports or ML
event, yet she is a senior ML engineer doing automated sports clipping. The conference tells you
where someone was on a Tuesday, not what they do. At weight 2 it is a tiebreaker.

**Industry alone is not enough.** Yuki Tanaka has maximum sports signal in the dataset — data
engineer at the NBA, previously Sportradar and Nielsen Sports — but his skills are Spark, dbt and
Airflow. He lands mid-pack for JOB001 and near the top for JOB004. That is the intended behaviour,
and it is why industry is capped at 13.

**A fourth observation shaped the notes component.** 41 of 75 attendees carry a note, and they land
on the right people — a human at the event was discriminating well. But the absence of a note does
not mean a person is irrelevant; it means nobody got to them. Weighting notes heavily would score
*how busy the booth was*, not candidate quality. Hence 10 points — enough to separate equals, not
enough to punish the unspoken-to — plus a separate `flagged_on_site` label so the human signal
survives independently of the number.

### 3.3 Where the model earns its cost

> **One principle governs every AI decision here: a model reads free text, rules compare structured
> fields.**

The supplied data is overwhelmingly structured — skills are delimited lists, experience is an
integer, industry is a single value. Comparing structured fields is arithmetic, and arithmetic is
exact, free, instant and explainable. Wrapping it in a model adds latency, cost and non-determinism
while producing a similarity number a recruiter cannot defend to a hiring manager.

There are exactly four places where free text exists:

| Touchpoint | Why a model belongs there | This submission |
| :---- | :---- | :---- |
| Non-standard skill names | no rule anticipates every alias | dictionary lookup, generated offline by a model |
| On-site field notes | free prose carrying real signal | token extraction against a domain vocabulary |
| Job descriptions | in production a role is a paragraph, not three tidy columns | parsed from CSV columns |
| Rationale and probes | infers what to ask on the call | template, or a live call behind `--llm` |

The rationale is worth the contrast. A template produces *"4/5 skills matched. Missing PyTorch. 7
years experience."* A model produces *"Seven years of real-time object detection at Mobileye — video
rather than sport. The vision work transfers on paper; whether the domain does is the open
question."* The second **infers**. It sits beside the component breakdown rather than replacing it;
removing the breakdown would return us to a black box.

**Nothing is a model call on the default path**, so the reviewer runs this without an API key and the
same candidate scores the same number on every run. A ranked shortlist that shifts between executions
cannot be defended. Full mechanics: `SPEC.md` §8.

**Measured effect, verifiable without a key.** The alias dictionary was generated by a model
(`build_aliases.py`, run once, result committed). Before it resolved "Sports CV" and "Event
Detection" against Marcus Reid's skills, he ranked 5th at 75, missing 3 of 5 required skills. After,
he ranks 2nd at 87, missing only AWS. **No scoring code changed** — the entire swing comes from the
generated dictionary.

Worth stating rather than hiding: three of the four touchpoints are neutralized here *because the
task supplied data already structured*. In a real deployment they carry real work.

### 3.4 Why not RAG, vector search, or an agent

Same principle. There is no unstructured corpus to retrieve over — the data is fields. At a few
thousand rows, tabular filtering returns in milliseconds, while vector retrieval adds
infrastructure, approximates where exact comparison is available, and returns a similarity score
with no explanation.

An agent framing is a reasonable way to *describe* this system, and each stage is isolated enough to
be lifted into a service. But handing the **scoring** to an autonomous agent would be a regression:
the same candidate may score 79 or 81 across runs, at added latency and per-candidate cost, in
exchange for an explanation the deterministic version already produces exactly. The brief requires
that a recruiter be able to say "she matches 4 of 5 required skills, missing PyTorch" — not "the
system said 80%".

---

## 4. Production

### 4.1 Integrations

Stated as requirements rather than implementations, since the exact API surfaces have not been
verified.

| Direction | System | Requirement |
| :---- | :---- | :---- |
| Read | HubSpot | event registrants as contact records, custom properties for talent-pool fields |
| Read | Comeet | open roles by id; candidate history by identifier |
| Write | Comeet | create a candidate on explicit recruiter action, with source attribution |
| Read | Enrichment provider | profile data keyed on profile URL, batched and queued |
| Write | Slack | referral request to the connected employee, with structured reply options |

**On HubSpot.** The talent-pool fields map onto contact properties, so the pool *extends* contact
records rather than creating a parallel database. Scoring lives in the pipeline; HubSpot remains the
system of record for the contact.

**On Comeet.** The stated constraint is that leads may not be held in the ATS. The distinction that
resolves it: *storing a pool* is not *entering a person*. The pool lives outside; an individual
enters only when a real process begins.

The scoring path is identical between this submission and production. That is what makes the ranking
defensible. Topology diagram and the per-step integration table: `SPEC.md` §9.

### 4.2 At scale

~30 conferences a year at 30–100 attendees gives 900–3,000 new records annually — 5,000–9,000 rows
after three years, against five recruiters and tens of concurrent roles. Well past where
spreadsheets collapse; far short of anything needing distributed infrastructure.

Flow A becomes independently scheduled workers with retries and queues, one per stage. Flow B stays
arithmetic over a table. Model cost is negligible: after exact and dictionary matching, a 100-person
event generates on the order of tens of resolution calls, twice a month, decaying as the dictionary
saturates.

---

## 5. Open questions

Stated rather than invented, because the right answers depend on how the team actually works.

- Conflicting referrals, when two colleagues disagree about the same candidate.
- Whether an `insufficient` response expires.
- The per-employee cap on referral requests.
- Whether the ATS constraint is regulatory, contractual or operational — the design assumes the most restrictive reading.
- Weight calibration, once hiring outcomes accumulate.
- Whether a thin unverified record should be sortable separately, given it can reach a high percentage on three components.
- Where the match percentage changes colour — a call for the people who read it daily.

## 6. What I would add with more time

A one-click referral request workflow with structured replies. Structured on-site annotation to
replace free-text notes, raising the ceiling on that signal. Pipeline status tracking from first
contact through process. Conference ROI analysis — the measurement that source tagging makes
possible. A candidate detail card behind the summary row. Outcome-driven weight calibration. And
employee past-company data, which would close the remaining referral gap.
