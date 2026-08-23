# README

Conference-attendee talent pool pipeline: enrich attendees once per event (Flow A), then rank the
pool against an open role on demand (Flow B). Full rationale is in `DECISIONS.md`, the technical
design is in `ARCHITECTURE.md`, and the exact implementation contract is in `SPEC.md`.

## Setup

Requirements: Python 3.10+, pandas only. Verified on Python 3.14.6 against both pandas 2.3.3 and
3.0.5 — all four jobs and the edge-case fixture produce byte-identical output on either.

**No API key is needed to run or evaluate this project.** `ingest`, `match` and `run` make no
network call of any kind and never import the `anthropic` package. There is one opt-in flag,
`match --llm`, that does call a model; it is off by default and everything below — every command,
every committed output file, the demo job, the edge-case fixture — is produced without it. If you
have no key, ignore the flag and nothing is missing.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` also lists `anthropic`, commented out. It is needed only by the two opt-in model
paths — `pipeline/build_aliases.py` and `match --llm` — and by neither flow's default run.

## Start here

**Open `index.html` at the repo root** (double click — no server, no API key). It lists the four open
roles with their headline numbers, and opening one goes straight to that role's recruiter view. There
is also a box to jump to a role by id. Every report it links is already committed, so the page works
on a fresh clone before you run anything.

`index.html` is generated, not hand-written: `match` and `run` refresh it, and
`python3 pipeline/main.py index` rebuilds it on its own. A role whose report hasn't been generated
yet renders as a card naming the command that produces it.

## How to run

Both flows live under `pipeline/` and share `data/` at the repo root as their source of truth. Run
from the repo root:

```bash
# Flow A — ingestion. Reads data/, writes pool/talent_pool.csv and pool/referral_edges.csv.
python3 pipeline/main.py ingest

# Flow B — matching. Reads pool/ + data/job_openings.csv, writes output/{JOB_ID}_matches.csv
# and output/{JOB_ID}_recruiter_view.html.
python3 pipeline/main.py match --job JOB001

# Both, in order.
python3 pipeline/main.py run --job JOB001

# Rebuild index.html, the landing page. Runs automatically after match/run.
python3 pipeline/main.py index
```

Every command above runs with no API key, no network access and no `anthropic` install.

One optional flag exists and is **not** needed to evaluate this project:

```bash
# Optional. Fills the ai_summary / ai_probes columns via the B7 seam.
# Requires ANTHROPIC_API_KEY and `pip install anthropic`. Without it those two
# columns stay empty and nothing else changes. See "Where the model is used".
python3 pipeline/main.py match --job JOB001 --llm
```

`match`/`run` fail with a clear, non-zero-exit message naming the `ingest` command if `pool/` is
empty, and with a message listing valid ids if `--job` doesn't match a row in
`data/job_openings.csv`.

Both flows are deterministic: running either one twice in a row produces byte-identical output
files, and Flow B makes no external call of any kind. This holds for the committed output too — a
clean checkout re-run reproduces every file under `output/` and `pool/` byte for byte. (The `--llm`
flag is the one exception to determinism, by nature; it is off by default and no committed file was
produced with it.)

Open `index.html` and pick a role, or open `output/JOB001_recruiter_view.html` directly in a browser
(double click — no server needed).
Each row's match percentage expands into the seven-component breakdown, and the weight sliders
re-rank live in browser memory only; they never write to disk, and a reset button restores the
default weights. The exported CSV always carries the default weights, so a shortlist attached to an
email means one fixed thing.

## Demo job_id

**`JOB001`** (Senior ML Engineer, AI/ML) is the demo job. Against the supplied 75-attendee pool it
produces `output/JOB001_matches.csv` and `output/JOB001_recruiter_view.html`, with **Lucas Evans**
ranked first at a match score of **89**.

The pipeline was also run against `JOB002`, `JOB003` and `JOB004` (see `data/job_openings.csv`), and
each one's `_matches.csv` and `_recruiter_view.html` is committed under `output/` alongside
`JOB001`'s. All four were verified deterministic — byte-identical across two consecutive runs, and
reproduced byte-for-byte by a clean checkout. Top 3 for each, with their seven component values
(`skills / title / experience / industry / notes / past / conference`, each `0–1`):

**JOB002 — Backend Engineer, Engineering, Mid-Senior**
| Rank | Candidate | Score | skills | title | experience | industry | notes | past | conference |
| :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| 1 | Chris Lee | 83 | 0.8333 | 1.0 | 1.0 | 1.0 | 0.5 | 0.0 | 0.0 |
| 2 | Liam Harris | 78 | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| 3 | Owen Jackson | 76 | 0.6667 | 0.7 | 1.0 | 1.0 | 0.5 | 1.0 | 0.0 |

**JOB003 — Senior Product Manager - Sports Data, Product, Senior**
| Rank | Candidate | Score | skills | title | experience | industry | notes | past | conference |
| :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| 1 | Elijah Allen | 80 | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 |
| 2 | Alex Turner | 80 | 0.75 | 0.7 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 |
| 3 | Scarlett Green | 66 | 0.25 | 0.55 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

**JOB004 — Data Engineer, Data, Mid**
| Rank | Candidate | Score | skills | title | experience | industry | notes | past | conference |
| :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| 1 | Scarlett Green | 95 | 0.8333 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 2 | Ryan Patel | 95 | 0.8333 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 3 | Yuki Tanaka | 88 | 1.0 | 0.7 | 1.0 | 1.0 | 0.5 | 1.0 | 1.0 |

(Yuki Tanaka's JOB004 result matches the case discussed in `DECISIONS.md` §3.2 — maximum sports
industry signal and now, against a data-engineering role, a perfect skills score too.)

## Where the model is used

**In short.** The pipeline runs end-to-end with no API key: `ingest`, `match` and `run` make no
network call and never import the `anthropic` package. Every committed output file was produced that
way.

The model appears in two opt-in places. `build_aliases.py` ran once at build time to generate
`data/skill_aliases.json`; its result is committed and its effect on the ranking is measurable
without re-running it — see the Marcus Reid case at the end of this section. `match --llm` is a
runtime flag that adds two free-text columns to the shortlist; without it those columns are empty and
nothing else changes, because scoring is never a model call.

This split is deliberate. A recruiter has to defend a shortlist to a hiring manager, so the score is
arithmetic over structured fields — exact, free, and explainable. The model handles the free text,
where it earns its cost. The reasoning is in `DECISIONS.md` §3.3; the rest of this section is the
detail.

---

Two places, **both opt-in, neither on the default path**. A default run — the commands under "How
to run", and every output file committed to this repository — calls no model and needs no key.

**1. `pipeline/build_aliases.py` — build time, already run.** Invoked manually as
`python pipeline/build_aliases.py`; not imported by `main.py`. It generated the current
`data/skill_aliases.json`, merging its output on top of the hand-written entries already in that
file (existing entries win on conflict). Its result is committed, so nobody needs to re-run it. Its
measured effect on the ranking is documented below.

**2. `pipeline/main.py match --job JOB001 --llm` — runtime, off by default.** This turns the B7 seam
live. It makes at most **21 calls per job**: one for each of the top 20 ranked candidates
(`output.apply_llm_briefs`, `shortlist_size=20`) filling that row's `ai_summary` / `ai_probes`, plus
one for the shortlist summary line above the recruiter view (`output.apply_llm_job_summary`). It
requires `ANTHROPIC_API_KEY` and `pip install anthropic`; `pipeline/llm.py` is imported only inside
the `if use_llm:` branch in `main.py`, so a run without the flag cannot reach it.

Everything the flag produces is **additive**. The deterministic `rationale` / `interview_probes` are
built by `build_match_row` first and are never overwritten; `llm.call_candidate_brief` and
`llm.call_shortlist_summary` both return `None` on a bad response or an API error, and the caller
treats `None` as normal — the field stays empty and the run completes. So a keyless run differs from
an `--llm` run in exactly two places: the `ai_summary` and `ai_probes` CSV columns are empty, and the
recruiter view renders no summary line above the list. Every score, every component value, every
skill list and every referral is identical, because **scoring is never a model call** — no component
function in `score.py` can reach `llm.py`, with or without the flag.

Four seam functions inside the pipeline mark where a model belongs in production. Three are
deterministic in every run; the fourth (B7) is the one `--llm` activates: `resolve_unknown_alias` (A3), the note-tag extractor `extract_note_tags` (A4),
the job parser `parse_job` (B1), and the rationale/probe builders `build_rationale` /
`build_interview_probes` (B7, one seam covering both). Each carries a docstring naming its
production model implementation, so the pipeline runs with no API key and returns identical output
on every run — see `ARCHITECTURE.md` §8 for the full model-boundary table.

**Measured effect.** Before `build_aliases.py` resolved "Sports CV" and "Event Detection" against
Marcus Reid's LinkedIn skills, he ranked 5th for JOB001 at a match score of 75, missing 3 of the 5
required skills (`Computer Vision`, `Object Detection`, `AWS`). With the two entries the generator
added — `sports cv -> Computer Vision` and `event detection -> Object Detection` — present in
`data/skill_aliases.json`, he ranks 2nd at 87, missing only `AWS`. No scoring code changed between
the two states; the entire swing comes from the generated alias dictionary. See
`docs/alias_generation_log.md` for the run that produced those entries.

## Design doc

- [`DECISIONS.md`](DECISIONS.md) — why: assumptions, scoring rationale, evidence from the data, the
  model boundary, production integrations, open questions. Wins on any disagreement with
  `ARCHITECTURE.md` or `SPEC.md`.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — flow diagrams, module map, data model, failure modes,
  production topology.
- [`SPEC.md`](SPEC.md) — the implementation contract: function signatures, exact scoring rules,
  exact column names.

## Assumptions

All assumptions and their rationale are in [`DECISIONS.md` §2](DECISIONS.md#2-assumptions) —
domain relevance as a weighted score rather than a filter, how an unverified contact is scored and
kept, how referral tiers and mutual-connection counts are treated as estimates rather than points,
ATS flagging, refresh cadence, trigger ownership, and privacy/GDPR handling.

## Edge case handling

**The unverified-profile branch (no LinkedIn match), and ten other edge cases the supplied data
never exercises, are now demonstrated by a fixture.** All 75 conference attendees in
`data/conference_attendees.csv` still match a row in `data/linkedin_profiles.csv` on `linkedin_url` —
confirmed by running Flow A and checking `pool/talent_pool.csv`, where every row has
`unverified = False` — so nothing in the *supplied* dataset takes the unverified path, and the same
is true for the other data-shape failure modes in `ARCHITECTURE.md` §10. `data/edge_cases/` adds a small,
unmistakably-fake synthetic fixture — the same seven filenames `data/` holds, plus the optional
`referral_feedback.csv` described below, with the real WSC employee roster reused where possible —
built to hit exactly the branches the supplied data leaves cold: no `linkedin_url` at all,
a `linkedin_url` matching no profile, empty `top_skills`, no note, no mutual connections and no shared
employer, a `past_titles` date that's missing or unparseable, an open-ended `(YYYY-present)` tenure, a
retired (`referral_feedback = insufficient`) referral edge, blank `years_experience`, a skill spelling
absent from the alias table, and a title in no `title_family`. Run it with:

```bash
python pipeline/main.py ingest --data-dir data/edge_cases
python pipeline/main.py match --job JOB001 --data-dir data/edge_cases
```

`--data-dir` namespaces `pool/` and `output/` by the data directory's name, so this never touches the
real `pool/` or `output/`; the fixture's own output is committed at `output/edge_cases/`. The
referral-specific states (worked together with dates, shared employer with no overlap, 3+ mutual
connections, 1–2, no referral path, a retired edge) are tabulated with exact recruiter-facing strings
in `DECISIONS.md` §2.3. See `DECISIONS.md` §2.2 and `ARCHITECTURE.md` §10 for the full statement of
these failure modes, including the two (an empty `pool/` directory, an unknown `job_id`) that are
CLI-argument failures rather than data shapes and so stay outside this fixture.

**A finding from that fixture:** the unverified candidate built to score well on title/note/
conference alone (`EC001 "Testcase Unverified"`) ranks **#1 of 13, at `match_score` 100** — strictly
ahead of every verified candidate in the fixture pool. The arithmetic is not an error:
`score.score_components` normalizes an unverified row over exactly the three weights it can compute
(title 25 + notes 10 + conference 2 = 37), and a candidate that maxes all three reaches 100 by
construction. No row in the supplied 75-attendee dataset triggers it.

The *presentation* was a real problem, and is fixed. A `100` next to a name, with the four
uncomputed components rendered as red "missing" chips, told a recruiter the opposite of the truth —
that this person had been assessed and found wanting on skills. The card now carries an
**Unverified** badge naming what the score is based on and stating that skills, experience, industry
and past companies are *unassessed, not absent*; the expandable breakdown lists only the three
scored components and labels the total `out of 37, not 100`; the gap chips are suppressed; and
`rationale` / `interview_probes` drop the `0/5 required skills matched` sentence for a line that
says the profile is missing and the first call has to establish the basics. `SPEC.md` §B6 carries
the full rule.

The ranking itself is left as-is deliberately — a thin record that matches on title and on what a
recruiter heard at the booth *is* worth a call; it is worth a call with the record's thinness on
screen, which is now what happens. `DECISIONS.md` §3.8 lists surfacing it as a sort option as an
open question.

## Executive summary for non-technical HR

Every month the team fills a room with exactly the engineers it wants to hire, and within days those
contacts are gone. This system turns each event into a permanent, searchable talent pool: every
attendee is enriched once and stored, and when a role opens the pool is ranked against it in
seconds. What a recruiter sees is a short list — the person, a match percentage, the skills they
have and the ones they're missing, and where one exists, the name of the colleague who already
knows them. Every score opens up to show how it was calculated, so the shortlist can be defended to
a hiring manager rather than trusted blindly. (`DECISIONS.md`, opening paragraph.)
