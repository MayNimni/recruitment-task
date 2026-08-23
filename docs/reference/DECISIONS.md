# Decisions — evidence and detail

This expands [`DESIGN.md`](../../DESIGN.md), which states each decision in summary form. Nothing
here repeats it; every section adds the evidence, the numbers or the reasoning behind a claim made
there. `SPEC.md` holds the implementation contract.

---

## 1. Evidence from the data

Four observations in the supplied files drove four decisions.

**Skill comparison must operate on meaning, not strings.** JOB001 requires Computer Vision and
Object Detection. Priya Anand (HS002) lists Python, OpenCV, YOLO, real-time processing, AWS and deep
learning — neither required skill by name. Under exact matching she scores 2 of 5. But YOLO *is* an
object-detection model and OpenCV *is* a computer-vision library, so under alias-resolved matching
she scores 4 of 5: roughly 12 points, and the difference between mid-pack and top-tier. This is why
`skill_aliases.json` exists and why it is config rather than code.

**Conference domain is a weak signal.** Chiara Russo (HS068) attended a broadcast expo — not a
sports or ML event — yet she is a senior ML engineer doing automated sports clipping with PyTorch
and computer vision. The conference tells you where someone was on a Tuesday, not what they do.
Hence weight 2: a tiebreaker, nothing more.

**Industry alone is not enough.** Yuki Tanaka (HS011) is a data engineer at the NBA, previously
Sportradar and Nielsen Sports — maximum sports signal in the dataset. But his skills are Spark, dbt
and Airflow: no PyTorch, no computer vision. He lands mid-pack for JOB001 and near the top for
JOB004. That is the intended behaviour, and it is why industry is capped at 13.

**Notes land on the right people, but their absence means nothing.** 41 of 75 attendees carry a
note, and they cluster on strong candidates — a human at the event was discriminating well. But the
absence of a note does not mean a person is irrelevant; it means nobody got to them. Weighting notes
heavily would score *how busy the booth was*, not candidate quality. Hence 10 points — enough to
separate equals, not enough to punish the unspoken-to — plus a separate `flagged_on_site` label so
the human signal survives independently of the number. If on-site staff recorded a structured
judgment instead of free prose, that weight could justifiably rise.

---

## 2. Referral grading — the numbers

`DESIGN.md` gives the four tiers and their pair counts. The distribution behind them:

**Connection counts barely separate anyone.** Of 75 attendees, **33 have no mutual connections, 37
have one or two, and only 5 reach three.** A signal where half the population sits in one bucket
cannot carry scoring weight, which is the empirical half of the argument for treating referrals as
context rather than points.

**Shared employers are rarer but sharper.** Token-bounded matching against dated `past_titles` finds
**20 candidate-employee pairs across 12 candidates.** Of those 20, **16 have tenures that actually
overlap** and 4 share a company the two people were never there together for. One of the 4 is a
dirty-data case: a malformed date range that still matches on company name but yields no parseable
dates, so it is treated as "no overlap" rather than guessed at.

**32 of 75 candidates have no edge at all.** They are still scored and ranked; the recruiter view
shows "No referral path". Missing a referral is not a mark against a candidate.

### Why matching must be token-bounded

Naive substring matching pairs a candidate from `Intel` with an employee from an
`IDF Intelligence Unit` — the substring is real, the relationship is not. Requiring a shared
non-generic token of four characters or more kills that pair while still matching the genuine
coincidence in this dataset, where two people really did both work at `IDF Intelligence Unit`.
The generic-token drop list and the plural-stemming rule are in `SPEC.md` §4, A6.

### Every referral string the view can render

| Condition | String shown | Fires on |
| :---- | :---- | :---- |
| Shared employer, tenures overlap | `worked together at Opta Sports, 2019-2021` | HS013 Marcus Reid |
| Shared employer, no overlap | `both worked at IDF Intelligence Unit, no overlapping years` | HS026 Grace Wilson |
| 3+ mutual connections | `3 mutual connections` | HS025 Lucas Evans |
| 1–2 mutual connections | `2 mutual connections` / `1 mutual connection` | HS068 Chiara Russo / HS044 Sara Lindqvist |
| No edge at all | `No referral path` | HS041 Viktor Novak |
| Best edge retired | falls through to the next-best edge | `data/edge_cases` EC008 |

The last row is not producible from the supplied CSVs — no `insufficient` value exists in them to
select against — which is why the fixture exists.

**Load.** One employee connects to 14 of the 75 attendees, so a per-employee request cap becomes
necessary well before the data volume does. The number is a team decision.

---

## 3. Why not RAG, vector search, or an agent

`DESIGN.md` states the principle: a model reads free text, rules compare structured fields. The
three rejected alternatives, and why:

**RAG and vector search.** There is no unstructured corpus to retrieve over — the data is fields. At
a few thousand rows, tabular filtering returns in milliseconds, while vector retrieval adds
infrastructure, approximates where exact comparison is available, and returns a similarity score
with no explanation attached.

**An autonomous agent.** The framing is reasonable as a *description* of this system — Flow A as an
ingestion agent, Flow B as a matching agent — and each stage is already isolated enough to be lifted
into a service. But handing the **scoring** to an agent would be a regression: the same candidate may
score 79 or 81 across runs, at added latency and per-candidate cost, in exchange for an explanation
the deterministic version already produces exactly. The brief requires a recruiter to be able to say
"she matches 4 of 5 required skills, missing PyTorch" — not "the system said 80%".

**A model in the loop at all, on the default path.** Two reasons it stays out: the reviewer runs
this without an API key, and the same candidate must score the same number on every run. A ranked
shortlist that shifts between executions cannot be defended to a hiring manager.

Worth stating rather than hiding: three of the four free-text touchpoints are neutralized here
*because the task supplied data already structured*. In a real deployment they carry real work.

---

## 4. On the weights

They are a reasoned starting point, not a derivation, and the honest position is to say so. Deriving
them would require hiring-outcome history showing which signals actually predicted a successful hire
— data that does not exist yet for this team.

What they encode is a **stated ordering**: skills and title dominate because they describe what a
person does; industry helps but does not decide; the conference barely matters. Two consequences
follow, and both are design features rather than concessions. The weights are **exposed as sliders**
in the recruiter view, because whether experience is negotiable for a given role is a recruiting
judgment rather than an engineering one. And once outcomes accumulate they can be **calibrated from
results** instead of intuition — which is only possible because the weights live in one dictionary
and are applied in one place.

---

## 5. On the unverified branch

`DESIGN.md` covers the rule. Two details behind it:

**Why no name-based fallback.** Joining on name where `linkedin_url` fails would raise coverage and
lower trust. Common names produce false matches, and attaching the wrong LinkedIn profile to a
candidate is worse than attaching none — it puts fabricated skills and experience into a shortlist a
recruiter will act on. The registration email remains a valid outreach path regardless.

**Why the presentation rules exist.** An unverified row normalized over 37 can reach 100, and in the
fixture it does — ranking #1 of 13. The arithmetic is correct; the *display* was not. A `100` beside
a name, with four uncomputed components rendered as red "missing" chips, told a recruiter the
opposite of the truth: that the person had been assessed and found wanting. The badge, the
`out of 37, not 100` label and the suppressed gap chips (`SPEC.md` §7) exist to close that gap
between a correct number and a misleading screen.
