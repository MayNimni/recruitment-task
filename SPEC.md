# SPEC.md — implementation contract

Companion to `DECISIONS.md`. That document explains *why*; this one fixes *what*, exactly, so the
implementation is unambiguous. Where the two disagree, `DECISIONS.md` wins and this file is wrong.

---

## 0. Repository layout

```
data/
  conference_attendees.csv      source
  linkedin_profiles.csv         source
  wsc_employees.csv             source
  job_openings.csv              source
  skill_aliases.json            alias -> canonical skill
  title_families.json           discipline head-nouns per department
  company_domains.json          sports / media keyword lists
pool/
  talent_pool.csv               written by Flow A
  referral_edges.csv            written by Flow A
output/
  JOB001_matches.csv            written by Flow B
  JOB001_recruiter_view.html    written by Flow B
data/edge_cases/                synthetic fixture: the seven files above plus
  referral_feedback.csv           optional 8th source, seeds retired edges (A8)
pool/edge_cases/                fixture pool, written by `--data-dir data/edge_cases`
output/edge_cases/              fixture output, same
pipeline/
  ingest.py  enrich.py  score.py  output.py  main.py
  llm.py                        B7 live seam, imported only under `match --llm`
  build_aliases.py              offline generator for skill_aliases.json
recruiter_view.html             HTML template copied by B6
index_template.html             landing-page template
index.html                      generated landing page — the reviewer's entry point
docs/alias_generation_log.md    record of the build_aliases.py run
requirements.txt  README.md  DECISIONS.md  ARCHITECTURE.md  SPEC.md
```

Python 3.10+, pandas only. **On the default path there are no network calls, no API keys and no
model calls at runtime** — that property is what the CLI below preserves, and it is what a reviewer
running this repo gets.

One opt-in exception: `match --llm` calls a model to fill `ai_summary` / `ai_probes` and the
recruiter view's summary line (§3, B7). It requires `ANTHROPIC_API_KEY` and the `anthropic` package,
is off by default, produced none of the committed output, and is additive — it cannot change a
score. `build_aliases.py` is the other model path; it runs offline, by hand, and its result
(`data/skill_aliases.json`) is committed.

## 1. Command line

```
python pipeline/main.py ingest                  # Flow A. reads data/, writes pool/
python pipeline/main.py match --job JOB001      # Flow B. reads pool/ + data/job_openings.csv, writes output/
python pipeline/main.py run   --job JOB001      # both, in order

python pipeline/main.py match --job JOB001 --llm            # + B7 live seam (needs ANTHROPIC_API_KEY)
python pipeline/main.py ingest --data-dir data/edge_cases   # any dir holding the same source files
python pipeline/main.py index                               # rebuild index.html (auto-run after match/run)
```

`--data-dir` namespaces the pool and output directories by that directory's name
(`pool/<name>/`, `output/<name>/`), so a fixture run can never overwrite the real one. The default
`data/` keeps `pool/` and `output/` exactly as above.

`match` fails with a clear message if `pool/` is empty, naming the `ingest` command. The two flows
never call each other in process — the pool files on disk are the only interface between them.

---

## 2. Flow A — ingestion

Runs once per person, per conference. No job is involved and no job may be referenced.

| Step | Module | Function | Result |
| :---- | :---- | :---- | :---- |
| A1 | `ingest.py` | `load_sources(data_dir)` | four dataframes plus three config dicts |
| A2 | `enrich.py` | `join_profiles(attendees, profiles)` | one row per attendee, `unverified` flag |
| A3 | `enrich.py` | `normalize_skills(raw_skills, aliases)` | canonical skill list |
| A4 | `enrich.py` | `extract_note_tags(note)` | tag list, `flagged_on_site` flag |
| A5 | `enrich.py` | `resolve_mutual_connections(row, employees)` | one edge per listed employee, `mutual_count` |
| A6 | `enrich.py` | `find_shared_employers(row, employees, ...)` | additional edges, `shared_employer` |
| A7 | `enrich.py` | `assign_tier(edge)` | `A` / `B` / `C` / `D` |
| A8 | `enrich.py` | `write_pool(pool_df, edges_df, pool_dir)` | two CSVs |

### A2 — join

Key is `linkedin_url`, exact string match after stripping whitespace and a leading `https://` or
`www.`. No name-based fallback — see `DECISIONS.md` §2.2.

A row with no match is **kept**, `unverified = True`, profile-derived fields empty. In the supplied
data all 75 rows match, so this branch is code and documentation, not a demonstrated path. Say so in
the README rather than implying it was exercised.

### A3 — skill normalization

`skill_aliases.json` maps lowercased alias to canonical name:

```json
{ "yolo": "Object Detection", "opencv": "Computer Vision", "ml": "Deep Learning",
  "ai": "Deep Learning", "machine learning": "Deep Learning", "torch": "PyTorch" }
```

Resolution order: exact case-insensitive match on the canonical set, then the alias table, then
`resolve_unknown_alias(token)`.

`resolve_unknown_alias` returns the token unchanged and carries this docstring, which is the visible
seam described in `DECISIONS.md` §3.3:

> Deterministic fallback. In production this function calls a language model to map an unseen skill
> name onto the canonical set, caches the result back into `skill_aliases.json`, and the model is
> never called twice for the same token. It is deterministic here so the pipeline runs with no API
> key and returns identical output on every run.

Keep both forms on the record: `skills_raw` and `skills_canonical`, plus `skills_alias_hits`, the
list of `raw -> canonical` pairs that the alias table resolved. The third one is what the recruiter
view shows as a blue chip.

### A4 — field notes

`extract_note_tags` lowercases the note, strips punctuation and stopwords, and returns the remaining
tokens plus their canonical forms via the alias table. `flagged_on_site = bool(note.strip())`.

Same seam pattern: a docstring stating that production replaces token extraction with a model that
returns structured signal tags, per `DECISIONS.md` §3.3.

Note **value** is not computed here. It depends on the job and belongs to Flow B.

### A5 — mutual connections

`wsc_mutual_connections` is a `;`-separated list of employee ids, possibly empty. For each id, emit
one edge carrying the employee's name, title and department from the roster. `mutual_count` is the
length of the list and is identical on every edge of that candidate.

### A6 — shared employers

Compare the candidate's `past_companies` against each employee's `work_history`, which carries
`Company (YYYY-YYYY);…`. Strip the date parenthesis, lowercase, split into tokens.

Stem every token longer than four characters by stripping a single trailing `s` before doing
anything else with it, and stem the drop-list itself the same way. This is a plural strip, not a
lemmatizer — `technologies` becomes `technologie`, not `technology` — and it must be applied
identically to both sides of the comparison, or `sports` on the drop-list and `sport` on a company
name silently stop being the same token.

Drop these generic tokens (post-stemming): `technologies, software, group, sports, lab, labs,
research, unit, freelance, startup, inc, ltd, media, digital, online, global, network, solution,
service, system, international, company, holding, studio, partner, venture, consulting, agency,
technology`.

Where a match is found, derive the overlap window: the candidate's tenure at that company comes from
`past_titles` (`Title at Company (YYYY-YYYY)`, the one candidate field that does carry dates), the
employee's from `work_history`. `overlap_years` is the length of the intersection in whole years and
`overlap_period` is its `YYYY-YYYY` string; both are `0` / empty when the ranges are disjoint or
either side is missing or unparseable. This is what lets B5 distinguish a proven shared tenure from a
merely shared employer.

A match requires a **shared non-generic token of four characters or more**. Substring matching is
forbidden: it pairs a candidate from `Intel` with an employee from `IDF Intelligence Unit`. That
example is named in `DECISIONS.md` §2.3 and must be a test case.

An edge may be created here for a candidate with zero mutual connections. That is the point of the
step — the pool contains such pairs.

### A7 — tier

| Tier | Condition |
| :---- | :---- |
| `A` | shared employer present, `mutual_count >= 1` |
| `B` | no shared employer, `mutual_count >= 3` |
| `C` | shared employer present, `mutual_count == 0` |
| `D` | no shared employer, `mutual_count` is 1 or 2 |

A shared employer corroborates a connection rather than substituting for one — it only reaches tier
`A` alongside at least one mutual connection; on its own it is tier `C` (`DECISIONS.md` §2.3). Tier
`D` is a stored value like the others: an edge that exists always carries one of these four tiers.
Only a candidate with no edge at all — no shared employer and no mutual connections — has no row.

### A8 — `pool/talent_pool.csv`

One row per attendee, keyed on `hubspot_id`:

`hubspot_id, full_name, email, company, title, linkedin_url, current_company, current_title,
location, years_experience, industry, past_companies, past_titles, skills_raw, skills_canonical,
skills_alias_hits, note_raw, note_tags, flagged_on_site, unverified, conference_name,
conference_domain, conference_date, source, first_seen_at, last_refreshed_at, ats_status,
pool_status`

List-valued columns are `;`-separated. `ats_status` is written empty — the field exists, the data
does not, and `DECISIONS.md` §2.4 already says so. `referral_feedback` is not a pool column: it is an
attribute of the candidate-employee pair, not of the candidate, so it lives only on
`pool/referral_edges.csv` — see `DECISIONS.md` §2.3 and the data model in `ARCHITECTURE.md` §6.

### A8 — `pool/referral_edges.csv`

One row per candidate-employee pair, keyed on `hubspot_id` + `employee_id`:

`hubspot_id, employee_id, employee_name, employee_title, employee_department, mutual_count,
shared_employer, overlap_years, overlap_period, tier, referral_feedback`

`shared_employer` holds the matched company name or is empty. `overlap_years` / `overlap_period`
carry the A6 window and are `0` / empty when there is none.

Write "worked together" **only** when `overlap_years > 0`. `past_companies` alone carries no dates,
so a shared employer on its own proves only that both were there at some point — B5 phrases that
case as "both worked at X, no overlapping years".

`referral_feedback` is not derived from any source record: in production a recruiter writes it back
through HubSpot after asking the colleague. Ingestion reads it from `referral_feedback.csv`
(`hubspot_id, employee_id, referral_feedback`) when that file is present in the data directory, and
writes `not_requested` for every edge when it is absent — as it is under `data/`.

---

## 3. Flow B — matching

Runs per open role. Arithmetic only: no file in `data/` is re-derived, no external call is made.

| Step | Module | Function |
| :---- | :---- | :---- |
| B1 | `score.py` | `parse_job(job_row, aliases)` |
| B2 | `main.py` | `read_pool(pool_dir)` |
| B3 | `score.py` | seven component functions, each returning a float in `[0, 1]` |
| B4 | `score.py` | `rank(scored_rows, weights)` |
| B5 | `output.py` | `select_referral(edges, job_department)` |
| B6 | `output.py` | `write_matches_csv(...)`, `write_recruiter_view(...)` |

### B1 — requirement set

From the job row: `required_skills` and `nice_to_have` split on `;` and normalized through the alias
table; `key_domains` split on `;` then tokenized; `seniority` mapped to a years threshold.

`domain_vocabulary` = key-domain tokens, plus their alias expansions, minus stopwords. Both the title
component and the conference component read this one vocabulary.

### B3 — the seven components

Every component returns a value in `[0, 1]`. Weight is applied once, in `rank`. A component never
returns points.

**Skills, weight 30.** `matched / len(required_skills)`, where a required skill counts as matched if
it appears in `skills_canonical`. Emit three lists for the output: `skills_matched` (exact on the raw
list), `skills_semantic` (matched only after alias resolution, as `raw -> canonical`), and
`skills_missing`.

**Title, weight 25.** `min(1.0, core + seniority_bonus)`.

Clean the title first: take the part before ` - `, lowercase, remove the tokens `senior, sr, staff,
principal, lead, head, of, junior, jr`.

| Core | Condition |
| :---- | :---- |
| `0.7` | the cleaned title **ends with** an entry from the job department's list in `title_families.json` |
| `0.25` | it does not, but the title contains a token from `domain_vocabulary` |
| `0` | neither |

| Bonus | Original title contains |
| :---- | :---- |
| `+0.3` | `senior`, `sr`, `staff` |
| `+0.15` | `principal`, `lead`, `head` |
| `0` | none of these |

`title_families.json` is keyed by the job's `department`. For `AI/ML`: `ml engineer, machine learning
engineer, ml research engineer, research engineer, ai engineer, computer vision engineer, cv
engineer, deep learning engineer, data scientist`. Populate `Engineering`, `Data` and `Product`
likewise so JOB002–JOB004 run.

Matching by `endswith` is deliberate: it reads the head noun of the title and ignores qualifiers, so
`senior computer vision engineer` and `sports data scientist` both land on their family.

**Experience, weight 15.** `1.0` at or above the threshold, otherwise `years / threshold`.
Thresholds: `Junior` 2, `Mid` 3, `Mid-Senior` 5, `Senior` 6.

**Industry, weight 13.** `1.0` if the profile's `industry` contains `sport`; `0.5` if it contains
`video`, `broadcast`, `streaming`, `media` or `ott`; `0` otherwise. Lowercased substring match on a
single short field is safe here — unlike company names, this field has no adversarial cases.

**Field notes, weight 10.** `1.0` if `note_tags` intersects `domain_vocabulary`; `0.5` if a note
exists with no intersection; `0` if no note.

**Past companies, weight 5.** Same two keyword lists as the industry component, read from
`company_domains.json`, applied to each entry in `past_companies`, taking the best: `1.0` sports,
`0.5` media or video, `0` otherwise.

**Conference domain, weight 2.** `1.0` if `conference_domain` tokens intersect `domain_vocabulary`,
else `0`.

### B3 — unverified candidates

If `unverified` is true, only `title`, `notes` and `conference` are computable. Sum only those
weights, divide by that sum rather than by 100, and set `score_basis` to the list of components used.
Missing data must not read as poor fit — `DECISIONS.md` §2.2.

### B4 — weights and ranking

Defaults: `skills 30, title 25, experience 15, industry 13, notes 10, past 5, conference 2`. They
live in one dictionary in `score.py` and are written into the CSV and the HTML from that same
dictionary. The CSV always carries default weights; see `DECISIONS.md` §3.3.

`match_score = round_half_up(sum(value * weight) / sum(weights) * 100)`, where `round_half_up(x) =
math.floor(x + 0.5)` rather than Python's built-in `round()`, which rounds half to even. This exists
to match `Math.round` in the recruiter view template, which rounds half up — otherwise a raw score
ending in exactly `.5` (e.g. `86.5`) would round to a different whole number in the CSV than on
screen. Sort descending, ties broken by `years_experience` then `hubspot_id` so runs are
reproducible.

At default weights, the recruiter view's initial render displays this same `match_score` value
directly rather than recomputing it in the browser, so the CSV and the screen cannot disagree at
the one point where they're compared. Recomputation still drives every render after a weight
slider moves, where a different number is expected.

### B5 — referral selection

Among that candidate's edges: highest tier first, then smallest department distance to the job's
department, then highest `mutual_count`. Department distance is a small explicit map — same
department is 0, a named adjacent pair is 1, anything else is 2. Retire any edge whose
`referral_feedback` is `insufficient` before choosing.

Carry `referral_why` as plain text. With a shared employer (tiers A and C):
`worked together at Mobileye, 2019-2021` when `overlap_years > 0`, otherwise
`both worked at Mobileye, no overlapping years`. With none: `3 mutual connections`
(singular at 1).

### B6 — `output/JOB001_matches.csv`

`job_id, hubspot_id, full_name, current_title, current_company, years_experience, location,
linkedin_url, match_score, match_score_after_feedback, score_basis, value_skills, value_title,
value_experience, value_industry, value_notes, value_past, value_conference, points_skills,
points_title, points_experience, points_industry, points_notes, points_past, points_conference,
skills_matched, skills_semantic, skills_missing, rationale, interview_probes, ai_summary, ai_probes,
referral_name,
referral_title, referral_department, referral_tier, referral_why, referral_feedback,
flagged_on_site, unverified, ats_status, conference_name, conference_date`

`ai_summary` and `ai_probes` sit immediately after `interview_probes`. They are written only by
`match --llm` and are empty on every default run — including every committed output file. They are
additive: the templated `rationale` / `interview_probes` beside them are always written first and are
never overwritten, and a failed model call leaves the pair empty rather than failing the run.

`match_score_after_feedback` equals `match_score` while no feedback exists. It is a separate column
because the adjustment sits on top of the fit score, never inside it — `DECISIONS.md` §2.3.

`rationale` and `interview_probes` are built by template, from the component values and the skill
lists. Both functions carry the same seam docstring as `resolve_unknown_alias`, naming the model
that replaces the template in production.

### B6 — `output/JOB001_recruiter_view.html`

One self-contained file, opened by double click, no server, data embedded as a JSON literal.

**Use `recruiter_view.html` (repo root) as the template.** It is the approved recruiter view — not
`index_template.html`, which is the landing page listing every role, nor the generated `index.html`. Copy it, then replace only the `DATA`
array, the header title and subtitle, and the `DEF` weights object. Do not redesign the markup, the
CSS or the interaction — they are approved. The sliders re-rank in browser memory only, never write
to disk, and the reset button restores `DEF`. The template file itself is never modified; the copy is
written to `output/JOB001_recruiter_view.html`.

**Unverified rows are labelled, not silently scored.** A row with `unverified = True` carries `u`
and `b` (its `score_basis`) in the `DATA` literal; a verified row carries neither and its payload is
unchanged. Given those, the view must:

- normalize the live weight-slider recompute over the components in `b` only, matching
  `score.score_components` — otherwise moving a slider would drop the candidate from its stated
  score to a full-100 denominator;
- list only those components in the expandable breakdown, name the rest `not scored`, and label the
  total `out of <sum of b's weights>, not 100`;
- suppress the red `<skill> missing` chips. With no profile there is no skill list to compare
  against, so a required skill is unassessed, not absent, and a red gap chip asserts otherwise;
- omit an absent `years_experience` or `location` from the meta line rather than rendering the gap;
- carry a plain-English badge saying no LinkedIn profile matched and what the score is based on.

`build_rationale` and `build_interview_probes` take the same branch: no `N/M required skills
matched` sentence and no `Probe depth on:` list, because both would report a comparison that never
happened. See `DECISIONS.md` §2.2 — missing data must not read as poor fit, on screen or in the CSV.

Both output files are written in the same call from the same in-memory rows, so the screen cannot
contradict the table.

---

## 4. What must be true when it is done

- `python main.py run --job JOB001` produces four files from a clean checkout with no configuration.
- Two consecutive runs produce byte-identical output.
- Every number in the recruiter view can be traced to `value × weight` in the CSV.
- A candidate with no edges renders as `No referral path` and is not dropped.
- No module reachable from a default run imports a network library. The two that can reach one
  (`llm.py`, `build_aliases.py`) do so inside a function, on an opt-in path — see `ARCHITECTURE.md` §7.
