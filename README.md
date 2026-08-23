# README

Conference-attendee talent pool pipeline: enrich attendees once per event (Flow A), then rank the
pool against an open role on demand (Flow B). Full rationale is in `DECISIONS.md`, the technical
design is in `ARCHITECTURE.md`, and the exact implementation contract is in `SPEC.md`.

## Setup

Requirements: Python 3.10+ (tested here on 3.14), pandas only — no network calls, no API keys, no
model calls at runtime.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` also lists `anthropic` as a commented-out, build-time-only dependency. It is
needed solely by `pipeline/build_aliases.py`, an offline script that regenerates
`data/skill_aliases.json`. It is not imported by `main.py` and is not required to run either flow.

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
```

`match`/`run` fail with a clear, non-zero-exit message naming the `ingest` command if `pool/` is
empty, and with a message listing valid ids if `--job` doesn't match a row in
`data/job_openings.csv`.

Both flows are deterministic: running either one twice in a row produces byte-identical output
files, and Flow B makes no external call of any kind.

Open `output/JOB001_recruiter_view.html` directly in a browser (double click — no server needed).
Each row's match percentage expands into the seven-component breakdown, and the weight sliders
re-rank live in browser memory only; they never write to disk, and a reset button restores the
default weights. The exported CSV always carries the default weights, so a shortlist attached to an
email means one fixed thing.

## Demo job_id

**`JOB001`** (Senior ML Engineer, AI/ML) is the demo job. Against the supplied 75-attendee pool it
produces `output/JOB001_matches.csv` and `output/JOB001_recruiter_view.html`, with **Lucas Evans**
ranked first at a match score of **89**.

The pipeline was also run against `JOB002`, `JOB003` and `JOB004` to confirm it handles every
supplied role without crashing (see `data/job_openings.csv` for the other three). Their output files
are not checked in — only `JOB001`'s two artifacts live under `output/`, per the demo job above — but
the run was verified deterministic (byte-identical across two consecutive runs) for all four jobs.
Top 3 for each, with their seven component values (`skills / title / experience / industry / notes /
past / conference`, each `0–1`):

**JOB002 — Backend Engineer, Engineering, Mid-Senior**
| Rank | Candidate | Score | skills | title | experience | industry | notes | past | conference |
| :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| 1 | Chris Lee | 83 | 0.8333 | 1.0 | 1.0 | 1.0 | 0.5 | 0.0 | 0.0 |
| 2 | Liam Harris | 78 | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 |
| 3 | Owen Jackson | 76 | 0.6667 | 0.7 | 1.0 | 1.0 | 0.5 | 1.0 | 0.0 |

**JOB003 — Senior Product Manager - Sports Data, Product, Senior**
| Rank | Candidate | Score | skills | title | experience | industry | notes | past | conference |
| :-- | :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| 1 | Elijah Allen | 72 | 0.25 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 |
| 2 | Alex Turner | 72 | 0.5 | 0.7 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 |
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

`pipeline/build_aliases.py` is the only place in this repository that calls a model. It runs at
build time — invoked manually as `python pipeline/build_aliases.py` — and is not part of either
pipeline flow: it's not imported by `main.py`, so `ingest` and `match` never call a model or make a
network request. This script generated the current `data/skill_aliases.json`, merging its output on
top of the hand-written entries already in that file (existing entries win on conflict).

Four seam functions inside the pipeline mark where a model belongs in production while staying
deterministic here: `resolve_unknown_alias` (A3), the note-tag extractor `extract_note_tags` (A4),
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

**The unverified-profile branch (no LinkedIn match), and five other edge cases the supplied data
never exercises, are now demonstrated by a fixture.** All 75 conference attendees in
`data/conference_attendees.csv` still match a row in `data/linkedin_profiles.csv` on `linkedin_url` —
confirmed by running Flow A and checking `pool/talent_pool.csv`, where every row has
`unverified = False` — so nothing in the *supplied* dataset takes the unverified path, and the same
is true for five of ARCHITECTURE.md §10's other failure-mode rows. `data/edge_cases/` adds a small,
unmistakably-fake synthetic fixture — same four filenames, the real WSC employee roster reused where
possible — built to hit exactly the branches the supplied data leaves cold: no `linkedin_url` at all,
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

**A finding from that fixture, not a bug to fix:** the unverified candidate built to score well on
title/note/conference alone (`EC001 "Testcase Unverified"`) ranks **#1 of 13, at `match_score` 100**
— strictly ahead of every verified candidate in the fixture pool. Nothing about this is a scoring
error; `score.score_components` normalizes an unverified row over exactly the three weights it can
compute (title 25 + notes 10 + conference 2 = 37), and a candidate that maxes all three reaches 100
by construction. It does mean a self-reported, never-enriched record can outrank verified ones on the
strength of a title and an on-site note alone — worth a second look before this ships, even though no
row in the supplied 75-attendee dataset happens to trigger it.

## Executive summary for non-technical HR

Every month the team fills a room with exactly the engineers it wants to hire, and within days those
contacts are gone. This system turns each event into a permanent, searchable talent pool: every
attendee is enriched once and stored, and when a role opens the pool is ranked against it in
seconds. What a recruiter sees is a short list — the person, a match percentage, the skills they
have and the ones they're missing, and where one exists, the name of the colleague who already
knows them. Every score opens up to show how it was calculated, so the shortlist can be defended to
a hiring manager rather than trusted blindly. (`DECISIONS.md`, opening paragraph.)
