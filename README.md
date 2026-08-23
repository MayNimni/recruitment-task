# Conference-attendee talent pool

Captures conference attendees as talent leads, enriches them with LinkedIn data once, and surfaces
the right candidates when a role opens.

**Two flows.** *Flow A — ingestion* runs once after each conference and writes a talent pool.
*Flow B — matching* runs when a role opens and ranks that pool against it. They share no state; the
pool on disk is the only interface.

📄 **[`DESIGN.md`](DESIGN.md)** — why this approach, production integrations, behaviour at scale,
assumptions, what I'd add with more time.

---

## Start here — nothing to install

### 🔗 **[maynimni.github.io/recruitment-task](https://maynimni.github.io/recruitment-task/)**

The live results, in your browser. It lists the four open roles with their headline numbers;
clicking one opens that role's recruiter view — the ranked shortlist, every score broken into its
seven components, and the colleague best placed to make an introduction.

Same thing offline: clone the repo and double-click `index.html` at the root. No server, no API key,
no install — every report is committed, so it works on a fresh clone before you run anything.

## Setup

**Requirements: Python 3.10+ and pandas. That is the entire dependency list.**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Verified on Python 3.14.6 against both pandas 2.3.3 and 3.0.5 — identical output on either.

> **No API key is required to run or evaluate this project.** `ingest`, `match` and `run` make no
> network call and never import the `anthropic` package. `requirements.txt` lists `anthropic`
> commented out; it is needed only by two opt-in paths described in
> [`DESIGN.md` §1](DESIGN.md#where-a-model-earns-its-cost), neither of which produced any committed
> file.

## How to run

From the repo root:

```bash
# Flow A — ingestion. Reads data/, writes pool/talent_pool.csv + pool/referral_edges.csv
python3 pipeline/main.py ingest

# Flow B — matching. Reads pool/ + data/job_openings.csv,
# writes output/JOB001_matches.csv + output/JOB001_recruiter_view.html
python3 pipeline/main.py match --job JOB001

# Both, in order
python3 pipeline/main.py run --job JOB001
```

| Command | Does |
| :---- | :---- |
| `ingest` | Flow A |
| `match --job JOB001` | Flow B |
| `run --job JOB001` | both |
| `index` | rebuild `index.html` (runs automatically after `match` / `run`) |

Both flows are **deterministic**: two consecutive runs produce byte-identical files, and a clean
checkout reproduces every committed artifact exactly.

`match` exits non-zero with a message naming `ingest` if the pool is empty, and lists the valid ids
if `--job` is unknown.

## Demo job_id

### ▶ **`JOB001`** — Senior ML Engineer, AI/ML, Senior

```bash
python3 pipeline/main.py run --job JOB001
```

Produces `output/JOB001_matches.csv` and `output/JOB001_recruiter_view.html` from the supplied
75-attendee pool. **Lucas Evans ranks first at a match score of 89.**

All four supplied roles were run and are committed under `output/`:

| Job | Role | Top match | Score |
| :--- | :---- | :---- | ---: |
| **JOB001** | Senior ML Engineer | Lucas Evans | 89 |
| JOB002 | Backend Engineer | Chris Lee | 83 |
| JOB003 | Senior Product Manager – Sports Data | Elijah Allen | 80 |
| JOB004 | Data Engineer | Scarlett Green | 95 |

## Output

Two files per role, written from the same in-memory rows so they cannot disagree.

**`output/JOB00N_matches.csv`** — one row per candidate, ranked. Identification, `match_score`, all
seven component values and their point contributions in their own columns, matched / semantic /
missing skills, a rationale sentence and interview probes, the referral contact and tier, the
`unverified` / `flagged_on_site` / `ats_status` flags, and provenance.

**`output/JOB00N_recruiter_view.html`** — self-contained, opens on a double click, no server. Each
match percentage expands into the seven-component breakdown, so any score can be traced to
`value × weight`. Weight sliders re-rank live **in browser memory only** — they never write to disk
and a reset button restores the defaults, so an exported CSV always means one fixed thing.

## Edge cases

`data/edge_cases/` is a 13-row synthetic fixture exercising ten branches the supplied data never
reaches — no LinkedIn match, an empty skill list, no note, no referral path, an unparseable tenure
date, a retired referral, a title in no known family.

```bash
python3 pipeline/main.py run --job JOB001 --data-dir data/edge_cases
```

`--data-dir` namespaces the pool and output directories, so a fixture run can never overwrite the
real one. Its output is committed at `output/edge_cases/`.

Notably, the unverified candidate ranks **#1 at a score of 100** — correct arithmetic, since an
unverified row is normalized over the three computable weights (`25+10+2 = 37`). Because a thin
record reaching 100 would otherwise mislead, the card carries an **Unverified** badge, the breakdown
labels the total `out of 37, not 100`, and unassessed skills are never shown as red "missing" chips.

## Repository

```
index.html          landing page — start here
DESIGN.md           the design document
data/               supplied CSVs + config, and the edge-case fixture
pipeline/           ingest · enrich · score · output · main
pool/               Flow A output
output/             Flow B output, one CSV + one HTML per role
docs/reference/     full architecture, implementation contract, decision log
```

[`docs/reference/`](docs/reference/) holds the long-form material `DESIGN.md` summarizes:
[`SPEC.md`](docs/reference/SPEC.md) (system shape, flow diagrams, exact scoring rules, column
contracts, data model, failure modes) and [`DECISIONS.md`](docs/reference/DECISIONS.md) (the full
reasoning behind every choice). Neither is required reading.
