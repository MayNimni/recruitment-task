# Decisions

Design decisions, assumptions and rationale for the conference-attendee talent pool pipeline.

Two companion documents carry the detail this one deliberately omits. `ARCHITECTURE.md` holds the
flow diagrams, the data model and the module map. `SPEC.md` holds the implementation contract —
function signatures, exact scoring rules, exact column names. This document explains why, and it
wins wherever the three disagree.

**In one paragraph, for a non-technical reader.** Every month the team fills a room with exactly the
engineers it wants to hire, and within days those contacts are gone. This system turns each event
into a permanent, searchable talent pool: every attendee is enriched once and stored, and when a role
opens the pool is ranked against it in seconds. What a recruiter sees is a short list — the person, a
match percentage, the skills they have and the ones they're missing, and where one exists, the name
of the colleague who already knows them. Every score opens up to show how it was calculated, so the
shortlist can be defended to a hiring manager rather than trusted blindly.

---

## 1. Pipeline Flow Diagram

The pipeline is **two flows on two triggers**, not one continuous run. This is the central
architectural decision. Diagrams are in `ARCHITECTURE.md`.

The rule that assigns work to a flow: anything true about a person regardless of any job belongs to
Flow A; anything measured against a specific job belongs to Flow B. So skill *normalization* is Flow
A and skill *overlap* is Flow B; note *tag extraction* is Flow A and note *scoring* is Flow B;
referral *edge construction* is Flow A and referral *selection* is Flow B.

### Flow A — ingestion, once per person, after each conference

| Step | Input | Process | Output |
| :---- | :---- | :---- | :---- |
| 1 | Four source files | Read attendees, profiles, employees, config | Raw rows |
| 2 | `linkedin_url` | Join attendee to profile | Merged record, or `unverified` flag |
| 3 | `top_skills` | Normalize to canonical skill names | Comparable skill list |
| 4 | `notes` | Extract topic tags from free text | Signal tags, `flagged_on_site` |
| 5 | `wsc_mutual_connections` | Resolve employee ids to names, titles, departments | Referral edges |
| 6 | `past_companies` × `work_history` | Match shared former employers | Additional referral edges |
| 7 | Referral edges | Grade relationship strength | Tiered edges |
| 8 | Merged record | Persist | Talent pool, two files |

All expensive work happens here, **once per person** — not once per person per job.

### Flow B — matching, when a role opens

| Step | Input | Process | Output |
| :---- | :---- | :---- | :---- |
| 1 | `job_id` | Parse role into skills, domains, seniority threshold | Requirement set |
| 2 | Talent pool | Load persisted profiles and edges | Scoring input |
| 3 | Profiles + requirements | Score seven components, each 0–1 | Component values |
| 4 | Component values | Normalize where a profile is missing | Comparable values |
| 5 | Component values | Apply weights, sum, rank | Ranked list |
| 6 | Referral edges | Select the path to surface for this role | Referral contact |
| 7 | Ranked list | Generate rationale and interview probes | Plain-language explanation |
| 8 | Ranked list | Write | Matches CSV + recruiter view |

Flow B makes **no external call of any kind**. It is arithmetic against pre-processed data, which is
why it stays fast as the pool grows. The two flows share no process state: the pool on disk is the
only interface between them, so ingestion can run today and matching next month.

---

## 2. Assumptions

### 2.1 How is domain relevance defined?

Not as a filter — as a **weighted, transparent score** across seven components. Nobody is excluded
before being scored.

Filtering early on title would have removed Priya Anand, whose title contains no "ML", before her
skills were ever read. She is the strongest candidate in the pool for JOB001.

### 2.2 What happens to a contact with no LinkedIn profile match?

**Kept, not dropped.** Without a profile only four components are computable — title, industry,
notes, conference — so scoring against the full 100 would penalize missing data rather than poor fit.
The score is normalized against the components actually available, and the record carries an
`unverified` flag.

Automated name-based lookup is deliberately **not** implemented: common names produce false matches,
and misidentifying a candidate is worse than not identifying one. The registration email remains a
valid outreach path.

Stated plainly: in the supplied data all 75 attendees match a profile. This branch is implemented and
documented but not exercised by the provided files.

### 2.3 Is one mutual connection the same as three?

No — but the answer is context, not points, and connection count is the weaker of two available
signals.

**Mutual connections carry zero scoring weight.** They say nothing about professional fit; a strong
candidate is strong with zero connections. What the team asked for is *who to ask*, so the output
names the person: "Ask Maya Levi, Senior ML Engineer, AI/ML."

The roster's `work_history` carries dates, and so — despite an earlier version of this document
claiming otherwise — does the candidate side: `past_titles` is `"Title at Company (YYYY-YYYY)"`,
open-ended as `"...-present"` for a current role. Once both sides are parsed, a shared former
employer stops being the strongest signal *available* and becomes something better: two people who
can be shown to have actually overlapped there, not merely both drawn a paycheck from the same
company at some point.

| Tier | Condition | What it means for the recruiter |
| :---- | :---- | :---- |
| A | Shared former employer, tenure dates confirmed to overlap, AND a mutual connection | Ask this person first — they overlapped, and know someone in common |
| B | No shared employer, 3+ mutual connections | Familiarity is plausible |
| C | 1–2 mutual connections (with or without a shared employer) — or a shared employer whose dates don't overlap but is corroborated by a mutual connection — or a shared employer with confirmed overlap but no mutual connection | Worth a careful ask |
| D | Shared former employer, no overlap, no mutual connection | Weakest signal that still clears the bar for a row |

Shared employment corroborates a connection, not a substitute for one — and now that overlap is
computable, "corroborates" means something stricter than it used to: a shared employer only reaches
tier A alongside *both* a mutual connection *and* confirmed overlapping tenure. A shared employer
whose dates don't overlap, or that has no mutual connection behind it, no longer outranks a plain
1–2-connection lead — both land at tier C, on the reasoning that neither is stronger evidence than
the other. Tier D — a shared employer with neither overlap nor a mutual connection — is a stored
value like the others, not the absence of a relationship. Only a candidate with no edge at all — no
shared employer and no mutual connections — has no referral row.

**What the data shows.** Of 75 attendees, 33 have no mutual connections, 37 have one or two, and only
5 reach three — so connection count alone separates almost nobody at the top. Shared-employer
matching against dated `past_titles` finds 20 candidate-employee pairs across 12 candidates. Of those
20: 16 have tenure dates that actually overlap, and 4 share a company the two people never
overlapped at (one of those 4 is a dirty-data case — a malformed date range that still matches on
company name but yields no parseable dates, so it's treated the same as "no overlap" rather than
guessed at). Overlap plus a mutual connection is what reaches tier A: 12 pairs. The remaining 8
shared-employer pairs split across tier C (6: 2 with no overlap but a mutual connection, 4 with
overlap but no mutual connection) and tier D (2: neither). Old tier D — no shared employer, 1–2
mutual connections — no longer exists as a separate bucket; it folds into tier C, since a bare 1–2
connections is no weaker a lead than a shared employer nobody can vouch for.

**Two limitations, stated — one resolved, one still open.** The first is resolved: candidate
`past_titles` *does* carry dates, so the output can now say "worked together at Mobileye,
2019-2021" once both people's tenures there are confirmed to overlap — not merely "both worked at
Mobileye". That stronger phrasing is only ever used when the dates back it up; a shared employer with
no confirmed overlap still reads as "both worked at X, no overlapping years". The second limitation
stands: matching must stay token-bounded, because naive substring matching would pair a candidate
from Intel with an employee from an IDF Intelligence Unit — both this document's earlier example and
this submission's data (Priya Anand vs. David Cohen) confirm the token-bounded match correctly finds
nothing there, while a real, separate coincidence (Grace Wilson and David Cohen genuinely both worked
at "IDF Intelligence Unit") still matches.

**Both signals are estimates; the truth comes from asking.** The pool therefore carries a
`referral_feedback` field with five states — `not_requested`, `pending`, `insufficient`, `positive`,
`reserved`. The adjustment sits **on top of** the match score rather than inside it, keeping the fit
score clean and making it possible to later measure whether referrals predicted good hires. Note the
implication of `insufficient`: three mutual connections who all answer "I don't really know them"
means those three connections were noise, and the path is retired so the same colleague is not asked
again. In the supplied data the field is unpopulated — every edge is `not_requested`, since nothing
has been asked yet, and nothing in Flow A ever writes `insufficient` (that's a later feedback loop
this submission doesn't implement). `data/edge_cases/` adds a small synthetic fixture — same four
filenames, same WSC roster — that ingests normally and then has one edge's `referral_feedback`
hand-patched to `insufficient`, specifically to exercise retirement end to end. Run against JOB001
(`python pipeline/main.py match --job JOB001 --data-dir data/edge_cases`), committed at
`output/edge_cases/`.

**Every referral state the recruiter view can render, the exact string it shows, and a candidate it
fires on** — five straight from the supplied JOB001 run; the sixth (retirement) only from the
`data/edge_cases` fixture, because no combination of the supplied CSVs produces an `insufficient`
edge — there's simply no `insufficient` value anywhere to select against:

| Condition | String shown | Candidate |
| :---- | :---- | :---- |
| Shared employer, tenures confirmed to overlap (tier A) | `worked together at Opta Sports, 2019-2021` | HS013 Marcus Reid |
| Shared employer, no overlapping years (tier C or D) | `both worked at IDF Intelligence Unit, no overlapping years` | HS026 Grace Wilson (tier D) |
| 3+ mutual connections, no shared employer (tier B) | `3 mutual connections` | HS025 Lucas Evans |
| 1–2 mutual connections (tier C) | `2 mutual connections` (singular form confirmed too: HS044 Sara Lindqvist gets `1 mutual connection`) | HS068 Chiara Russo |
| No shared employer and no mutual connections — no `referral_*` row at all | recruiter view: "No referral path" | HS041 Viktor Novak |
| Best edge retired (`referral_feedback = insufficient`) | before retirement: `worked together at Mobileye, 2019-2021` (Maya Levi, tier A) — after: `3 mutual connections` (Itai Nahum, tier B) | `data/edge_cases` fixture, EC008 "Edge Retired" — not producible from the supplied CSVs |

Where several employees connect to one candidate, the one whose department is closest to the role is
surfaced first. One employee connects to 13 of the 75 attendees, so a request cap will be needed —
the number is a team decision.

### 2.4 Should candidates already in the ATS be flagged?

Yes. Reaching out to someone rejected two months ago costs credibility. The pipeline emits an
`ats_status` flag, populated in production by a **read-only** lookup returning whether a process
exists, its outcome and its date. Here the column exists and is empty.

### 2.5 What is the refresh cadence?

Ingestion after each conference, 2–3 times a month. Matching on demand when a role opens. Profile
refresh every 6–12 months, since experience and skills change slowly. Records archived or deleted
after 36 months without interaction.

### 2.6 Who triggers the pipeline?

Ingestion is automatic on registration export or event close. Matching is recruiter-initiated. The
ATS handoff is manual by design: consent is the trigger and is not machine-detectable, a misfiring
automation would push hundreds of unqualified leads into a system explicitly not meant for them, and
a small team accepts a button it controls faster than a system that moves people without asking.

Manual does not mean laborious — one click creates the record, populates known fields, tags the
originating conference and flips pool status. **Tagging source closes the measurement loop**: once
conference origin is recorded on hires, the team can finally answer which events produce placements.

### 2.7 Privacy and GDPR

A registrant consented to attend an event, not to join a candidate database. Three decisions follow.
The talent pool **lives outside the ATS**, which is for people who applied. The registration form
should carry a **soft opt-in** — a low-pressure question about openness to hearing about roles, which
identifies warm leads and establishes a lawful basis for later outreach. And retention, provenance
and deletion are mandatory at scale: each record stores its source event and capture date, and honors
deletion requests.

LinkedIn's terms prohibit scraping, so production enrichment goes through a licensed provider.

---

## 3. Design Rationale

### 3.1 Scoring methodology

Two steps, no more than that. Every component produces a value between 0 and 1; each is multiplied by
its weight and summed. The weights are the ceiling each component can contribute at a perfect match.

| Component | Weight | How the 0–1 value is computed |
| :---- | :---- | :---- |
| Skill overlap | 30 | matched required skills ÷ total required skills |
| Title match | 25 | discipline head-noun of the role, plus a seniority bonus |
| Years of experience | 15 | 1.0 at or above the seniority threshold; scaled below it |
| Industry | 13 | 1.0 sports or sports-media; 0.5 adjacent video, broadcast, streaming; 0 otherwise |
| Field notes | 10 | 1.0 if the note references a key domain; 0.5 if generic; 0 if absent |
| Past companies | 5 | 1.0 sports; 0.5 media or video; 0 otherwise |
| Conference domain | 2 | 1.0 if the event domain overlaps the key domains; 0 otherwise |

Seniority thresholds: junior 0–2 years, mid 3–5, senior 6+. Exact rules for all seven, including the
title sub-rule, are in `SPEC.md` §3.

**Why components return 0–1.** It separates measurement from policy. The component states a fact —
four of five required skills is 0.80. The weight states a judgment — what that fact is worth for this
role. Keeping them apart is what lets the weight sliders re-rank instantly without recomputing, what
makes the breakdown readable as `value × weight = points`, and what makes normalization for missing
data possible at all.

**Why these weights.** Honest answer: they are a reasoned starting point, not a derivation. Deriving
them would require hiring outcome history showing which signals actually predicted a successful hire.
What they encode is a stated ordering — skills and title dominate because they describe what a person
does, industry helps but doesn't decide, the conference barely matters. Two consequences follow. The
weights are **exposed as sliders in the recruiter view**, because whether experience is negotiable
for a given role is a recruiting judgment, not an engineering one. And once outcomes accumulate they
can be calibrated from results rather than intuition.

### 3.2 Evidence from the data

Three cases drove three decisions.

**Skill comparison must operate on meaning, not strings.** JOB001 requires Computer Vision and Object
Detection. Priya Anand (HS002) lists Python, OpenCV, YOLO, real-time processing, AWS, deep learning.
Under exact matching she scores 2 of 5. But YOLO *is* an object detection model and OpenCV *is* a
computer vision library — under meaning-based matching she scores 4 of 5, worth roughly 12 points and
the difference between mid-pack and top-tier.

**Conference domain is a weak signal.** Chiara Russo (HS068) attended a broadcast expo, not a sports
or ML event, yet she is a senior ML engineer doing automated sports clipping with PyTorch and
computer vision. The conference tells you where someone was on a Tuesday, not what they do. At weight
2 it is a tiebreaker.

**Industry alone is not enough.** Yuki Tanaka (HS011) is a data engineer at the NBA, previously
Sportradar and Nielsen Sports — maximum sports signal in the dataset. But his skills are Spark, dbt
and Airflow: no PyTorch, no computer vision. He lands mid-pack for JOB001 and would rank near the top
for JOB004. That is the intended behaviour, and it is why industry is capped at 13.

**A fourth observation shaped the notes component.** Notes land on the right people — Lucas Evans,
Marcus Reid, Priya Anand all carry substantive notes; Ben Walker, Zoe Wright and Amelia King carry
none. A human at the event was discriminating well, and 41 of 75 rows carry a note. But the asymmetry
matters: the absence of a note does not mean a person is irrelevant, it means nobody got to them.
Weighting notes too heavily would score *how busy the booth was*, not candidate quality. So 10 points
— enough to separate equals, not enough to punish the unspoken-to — plus a separate `flagged_on_site`
label so the human signal survives independently of the number. If on-site staff recorded a
structured judgment instead of free text, that weight could justifiably rise.

### 3.3 Where the model earns its cost

**One principle governs every AI decision here: a model reads free text, rules compare structured
fields.**

The provided data is overwhelmingly structured — skills are delimited lists, experience is an
integer, industry is a single value. Comparing structured fields is arithmetic, and arithmetic is
exact, free, instant and explainable. Wrapping it in a model adds latency, cost and non-determinism
while producing a similarity number a recruiter cannot defend to a hiring manager.

There are exactly four places where free text exists.

| Touchpoint | Why a model | This submission |
| :---- | :---- | :---- |
| Non-standard skill names | no rule anticipates every alias | dictionary lookup in a config file |
| On-site field notes | free prose carrying real signal | token extraction against a domain vocabulary |
| Job descriptions | in production a role is a paragraph, not three tidy columns | parsed from CSV columns |
| Rationale sentence | infers what to probe on the call | template over component values |

The rationale is worth the contrast. A template produces *"4/5 skills matched. Missing PyTorch. 7
years experience."* A model produces *"Computer vision engineer with seven years in real-time object
detection from Mobileye. Her experience is in video rather than sports — worth probing how well that
transfers."* The second infers. It supplements the component breakdown rather than replacing it;
removing the breakdown would return us to a black box.

**No model is called at runtime in the submitted code.** Three functions carry the boundary
explicitly — a working rule-based implementation plus a docstring naming what replaces it in
production. Two reasons: the reviewer runs this without an API key, and the same candidate must score
the same number on every run. A ranked shortlist that shifts between executions cannot be defended.

Worth stating rather than hiding: three of the four touchpoints are neutralized here because the task
supplied data already structured. In a real deployment they carry real work.

**The alias dictionary and the title and company keyword lists live in configuration files, not in
code.** Extending them is a recruiting act, not an engineering one.

### 3.4 Why not RAG, vector search, or an agent

Same principle. There is no unstructured corpus to retrieve over — the data is fields. At a few
thousand rows tabular filtering returns in milliseconds, while vector retrieval adds infrastructure,
approximates where exact comparison is available, and returns a similarity score with no explanation.

An agent framing is a reasonable way to *describe* this system — Flow A as an ingestion agent with
three tools, Flow B as a matching agent — and each stage is already isolated enough to be lifted into
a service. But handing the *scoring* to an autonomous agent would be a regression: the same candidate
may score 79 or 81 across runs, at added latency and per-candidate cost, in exchange for an
explanation the deterministic version already produces exactly. The brief requires that a recruiter
be able to say "she matches 4 of 5 required skills, missing PyTorch" — not "the system said 80%".

### 3.5 Production integrations

Stated as requirements rather than implementations, since the exact API surfaces have not been
verified.

| Direction | System | Requirement |
| :---- | :---- | :---- |
| Read | HubSpot | Event registrants as contact records, with custom properties for talent-pool fields |
| Read | Comeet | Open roles by id, and candidate history by identifier |
| Write | Comeet | Create a candidate on explicit recruiter action, with source attribution |
| Read | Enrichment provider | Profile data keyed on profile URL, batched and queued |
| Write | Slack | Referral request to the connected employee, with structured reply options |

**On HubSpot.** The talent-pool fields map onto contact properties, so the pool extends contact
records rather than creating a parallel database. Scoring lives in the pipeline; HubSpot remains the
system of record for the contact.

**On Comeet.** The stated constraint is that leads may not be held in the ATS. The distinction that
resolves it is between *storing a pool* and *entering a person*: the pool lives outside, and an
individual enters only when a real process begins.

Note what does **not** change between this submission and production: steps 3 through 7 of Flow B —
the entire scoring path — are identical. That is what makes the ranking defensible.

### 3.6 Behaviour at scale

Roughly 30 conferences a year at 30–100 attendees gives 900–3,000 new records annually, so 5,000–9,000
rows after three years, against five recruiters and tens of concurrent roles. Well past where
spreadsheets collapse; far short of anything needing distributed infrastructure.

At that size Flow A becomes independently scheduled workers with retries and queues, one per
ingestion stage; Flow B stays arithmetic over a table. Model cost is negligible — after exact and
dictionary matching, a 100-person event generates on the order of tens of resolution calls, twice a
month, decaying as the dictionary saturates.

### 3.7 Output

One row per candidate, ranked: identification, match score, the seven component values in their own
columns, matched and semantic and missing skills, the rationale sentence and interview probes, the
referral contact and feedback state, the `unverified`, `flagged_on_site` and `ats_status` flags, and
provenance — profile URL, conference name and date.

The pipeline emits **four files across two flows**: the pool and its referral edges from Flow A, the
ranked table and a self-contained recruiter view from Flow B. The view opens on a double click with
no server. Each row's match percentage is clickable and expands into the component breakdown, and
weight sliders re-rank live. The design constraint is that a recruiter decides in under two minutes
whether to call.

**The sliders re-rank in browser memory only.** They never write to disk and are not persisted; a
reset button restores the defaults, so the exported table always carries the default weights and a
shortlist attached to an email means one fixed thing. Both Flow B files are written in the same call
from the same rows, so the screen cannot contradict the table.

### 3.8 Open questions

Stated rather than invented, because the right answers depend on how the team actually works.
Conflicting referrals when two colleagues disagree. Whether an `insufficient` response expires.
The per-employee cap on referral requests. Whether the ATS constraint is regulatory, contractual or
operational — the design assumes the most restrictive reading. Weight calibration once hiring
outcomes accumulate. And where the match percentage changes colour, which should be set by the people
who read it daily.

### 3.9 What I would add with more time

A one-click referral request workflow with structured replies. Structured on-site annotation to
replace free-text notes, raising the ceiling on that signal. Pipeline status tracking from first
contact through process. Conference ROI analysis, which is the measurement the source tagging makes
possible. A candidate detail card behind the summary row. Outcome-driven weight calibration. And
employee past-company data, which would close the remaining referral gap.
