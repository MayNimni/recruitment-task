"""Flow B steps 5-8: referral selection, rationale/probe templates, and the
two output writers. output.py must not import score.py or enrich.py
(docs/reference/SPEC.md §8) — everything it needs arrives as plain data from main.py.
apply_llm_briefs is the one exception: it imports llm.py, the standalone B7
model seam, lazily and only when called (main.py's `match --llm` path) — llm.py
is not a Flow A/B step owner, just an isolated model-call boundary, so it is
not a "peer" in the §7 sense.
"""

import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}

# B5 department-distance map. Not specified anywhere in the docs beyond "a
# small explicit map" (docs/reference/SPEC.md §B5) naming same-department=0 and a named
# adjacent pair=1, so this is a judgment call, made explicit here rather than
# buried in a lookup: departments that routinely work the same roadmap at a
# sports-tech company are adjacent, everything else (including HR, Sales,
# Content, Leadership) is distance 2.
DEPARTMENT_ADJACENCY = {
    frozenset({"AI/ML", "Engineering"}),
    frozenset({"AI/ML", "Data"}),
    frozenset({"Engineering", "Data"}),
    frozenset({"Product", "Engineering"}),
    frozenset({"Product", "Data"}),
}

MATCHES_COLUMNS = [
    "job_id", "hubspot_id", "full_name", "current_title", "current_company",
    "years_experience", "location", "linkedin_url", "match_score",
    "match_score_after_feedback", "score_basis", "value_skills", "value_title",
    "value_experience", "value_industry", "value_notes", "value_past",
    "value_conference", "points_skills", "points_title", "points_experience",
    "points_industry", "points_notes", "points_past", "points_conference",
    "skills_matched", "skills_semantic", "skills_missing", "rationale",
    "interview_probes", "ai_summary", "ai_probes", "referral_name", "referral_title",
    "referral_department", "referral_tier", "referral_why",
    "referral_feedback", "flagged_on_site", "unverified", "ats_status",
    "conference_name", "conference_date",
]


def department_distance(dept_a: str, dept_b: str) -> int:
    if dept_a == dept_b:
        return 0
    if frozenset({dept_a, dept_b}) in DEPARTMENT_ADJACENCY:
        return 1
    return 2


def select_referral(edges: list, job_department: str) -> list:
    """B5. Filters out edges retired via referral_feedback == 'insufficient',
    then orders the rest: highest tier first, then smallest department
    distance to job_department, then highest mutual_count. Returns the full
    ordered list (not just the winner) — the recruiter view shows every
    edge for a candidate, with element 0 being the one the CSV surfaces as
    the single referral_* column set.
    """
    usable = [e for e in edges if e.get("referral_feedback") != "insufficient"]

    def key(edge):
        return (
            TIER_RANK.get(edge["tier"], 99),
            department_distance(edge["employee_department"], job_department),
            -int(edge["mutual_count"]),
        )

    return sorted(usable, key=key)


def referral_why(edge: dict) -> str:
    """'worked together at X, YYYY-YYYY' when the matched company's tenures
    actually overlap (overlap_years > 0), 'both worked at X, no overlapping
    years' for a shared employer whose tenures never coincided, 'N mutual
    connections' otherwise. "worked together" is unreachable when
    overlap_years is 0 — it can only be reported once dates confirm it."""
    shared = edge.get("shared_employer")
    overlap_years = int(edge.get("overlap_years") or 0)
    if shared and overlap_years > 0:
        return f"worked together at {shared}, {edge.get('overlap_period', '')}"
    if shared:
        return f"both worked at {shared}, no overlapping years"
    n = int(edge["mutual_count"])
    return f"{n} mutual connection{'' if n == 1 else 's'}"


def build_rationale(pool_row: dict, requirements: dict, values: dict, matched: list, missing: list) -> str:
    """Deterministic template over component values. In production this function calls a language
    model that reads the full candidate record and infers what to say — e.g. noting that a
    candidate's video-industry experience may not transfer to sports, a judgment a template cannot
    make. It is deterministic here so the pipeline runs with no API key and returns identical output
    on every run.
    """
    n_required = len(requirements["required_skills"])

    # An unverified row has no enriched profile, so skills_canonical is empty and
    # every required skill lands in `missing` — but nothing was compared, so
    # "0/N matched" would state a finding the data does not support. Say what is
    # actually known instead (docs/reference/SPEC.md §B3, unverified branch).
    if pool_row.get("unverified"):
        parts = [
            "No LinkedIn profile matched this contact — scored on title, field notes and "
            "conference domain only. Skills and experience are unassessed, not absent."
        ]
    else:
        parts = [f"{len(matched)}/{n_required} required skills matched."]
        if missing:
            parts.append(f"Missing {', '.join(missing)}.")

    years = pool_row.get("years_experience")
    if years is not None and not (isinstance(years, float) and math.isnan(years)):
        years_int = int(years)
        parts.append(f"{years_int} year{'s' if years_int != 1 else ''} experience.")

    industry_value = values.get("industry")
    if industry_value == 1.0:
        parts.append("Sports industry background.")
    elif industry_value == 0.5:
        parts.append("Adjacent media/broadcast background, not sports-native.")

    return " ".join(parts)


def build_interview_probes(pool_row: dict, requirements: dict, values: dict, missing: list) -> str:
    """Deterministic template over component values. In production this function calls a language
    model that infers what to probe on the call from the full candidate record. It is deterministic
    here so the pipeline runs with no API key and returns identical output on every run.
    """
    probes = []

    # Same reason as build_rationale: with no profile there is no skill list to
    # find gaps in, so the first call has to establish the record, not probe it.
    if pool_row.get("unverified"):
        required = ", ".join(requirements["required_skills"])
        probes.append(
            f"No profile on file — establish the basics first: current role, years of "
            f"experience, and depth on {required}."
        )
    elif missing:
        probes.append(f"Probe depth on: {', '.join(missing)}.")

    industry_value = values.get("industry")
    if industry_value is not None and industry_value < 1.0:
        probes.append("Confirm how directly their industry background transfers to sports.")

    if not pool_row.get("flagged_on_site"):
        probes.append("No on-site note on file — confirm domain interest and motivation from scratch.")

    if not probes:
        probes.append("No structural gaps identified from the data — validate depth in conversation.")

    return " ".join(probes)


def _blank_if_none(value):
    return "" if value is None else value


def _exact_or_none(value):
    """Component values and points are written at full precision, not rounded.

    They are exact fractions (experience is 5/6 for a 5-year candidate against
    a 6-year threshold) that don't terminate in binary floating point. Rounding
    them for looks breaks the one contract this output exists to keep: a score
    must reproduce from its own published columns. Four of the 75 JOB001
    candidates land on a total of exactly 36.5 / 20.5 / 18.5, which rounds up
    to the printed match_score — but 0.8333 x 15 is 12.4995, so a `value x
    weight` check against a rounded column produced a number one point below
    the score printed beside it.

    Nothing renders these raw: the recruiter view formats every component with
    toFixed(2) before it reaches the screen, and the CSV's own match_score
    column is already an integer. Precision here is for the audit, not the eye.
    """
    return None if value is None else value


def build_match_row(job_row, pool_row: dict, requirements: dict, scored: dict, candidate_edges: list) -> dict:
    """Assembles one output/JOB001_matches.csv row from a scored candidate.
    scored carries 'values', 'points', 'score_basis', 'match_score' (from
    score.rank). candidate_edges is that hubspot_id's slice of
    referral_edges.csv, already as plain dicts.
    """
    values = scored["values"]
    points = scored["points"]
    matched, semantic, missing = pool_row["_skills_lists"]

    ordered_edges = select_referral(candidate_edges, requirements["department"])
    top_edge = ordered_edges[0] if ordered_edges else None

    rationale = build_rationale(pool_row, requirements, values, matched, missing)
    probes = build_interview_probes(pool_row, requirements, values, missing)
    meaning = note_meaning(pool_row, requirements)

    row = {
        "job_id": requirements["job_id"],
        "hubspot_id": pool_row["hubspot_id"],
        "full_name": pool_row["full_name"],
        "current_title": pool_row.get("current_title") or pool_row.get("title") or "",
        "current_company": pool_row.get("current_company") or pool_row.get("company") or "",
        "years_experience": _blank_if_none(pool_row.get("years_experience")),
        "location": pool_row.get("location") or "",
        "linkedin_url": pool_row.get("linkedin_url") or "",
        "match_score": scored["match_score"],
        "match_score_after_feedback": scored["match_score"],
        "score_basis": ";".join(scored["score_basis"]),
        "skills_matched": ";".join(matched),
        "skills_semantic": ";".join(f"{r} -> {c}" for r, c in semantic),
        "skills_missing": ";".join(missing),
        "rationale": rationale,
        "interview_probes": probes,
        "ai_summary": "",
        "ai_probes": "",
        "referral_name": top_edge["employee_name"] if top_edge else "",
        "referral_title": top_edge["employee_title"] if top_edge else "",
        "referral_department": top_edge["employee_department"] if top_edge else "",
        "referral_tier": top_edge["tier"] if top_edge else "",
        "referral_why": referral_why(top_edge) if top_edge else "",
        "referral_feedback": top_edge["referral_feedback"] if top_edge else "",
        "flagged_on_site": pool_row.get("flagged_on_site", False),
        "unverified": pool_row.get("unverified", False),
        "ats_status": pool_row.get("ats_status") or "",
        "conference_name": pool_row.get("conference_name") or "",
        "conference_date": pool_row.get("conference_date") or "",
    }
    for name in ("skills", "title", "experience", "industry", "notes", "past", "conference"):
        row[f"value_{name}"] = _blank_if_none(_exact_or_none(values[name]))
        row[f"points_{name}"] = _blank_if_none(_exact_or_none(points[name]))

    # Consumed by write_recruiter_view, dropped before the CSV write (MATCHES_COLUMNS
    # doesn't include them, so building the CSV frame from that fixed column list ignores them).
    row["_ordered_edges"] = ordered_edges
    row["_values"] = values
    row["_note_meaning"] = meaning
    return row


def _build_job_summary(requirements: dict) -> dict:
    """The THE ROLE side of the B7 prompt (llm.py) — the six fields named in the
    model spec, already sitting on `requirements` from score.parse_job (B1).
    """
    return {
        "title": requirements["job_title"],
        "department": requirements["department"],
        "seniority": requirements["seniority"],
        "key_domains": requirements["key_domains"],
        "required_skills": requirements["required_skills"],
        "nice_to_have": requirements["nice_to_have"],
    }


def note_meaning(pool_row: dict, requirements: dict):
    """score_notes' domain-overlap check (docs/reference/SPEC.md §B3) in words, or None when
    there is no on-site note. Pure set arithmetic over note_tags — no model,
    no network — so it is computed on every run, in build_match_row, and both
    the recruiter view and the B7 prompt read the one stashed value.
    """
    if not pool_row.get("flagged_on_site", False):
        return None
    tags = set(pool_row.get("note_tags") or [])
    return (
        "matches this role's domain" if tags & requirements["domain_vocabulary"]
        else "no overlap with this role's domain"
    )


def _build_candidate_record(pool_row: dict, requirements: dict, values: dict) -> dict:
    """The THE CANDIDATE side of the B7 prompt. Reuses '_skills_lists' (set by
    main.py before scoring) and this row's already-rounded component values —
    no scoring logic is re-derived here, only reshaped for the model. note_meaning
    is read from the module-level helper of that name — the same value the
    recruiter view shows — since the raw 0/0.5/1.0 is already in
    component_values and wouldn't tell the model anything it doesn't.
    """
    matched, semantic, missing = pool_row["_skills_lists"]
    flagged = pool_row.get("flagged_on_site", False)
    years = pool_row.get("years_experience")
    years_out = None if years is None or (isinstance(years, float) and math.isnan(years)) else years

    return {
        "current_title": pool_row.get("current_title") or pool_row.get("title") or "",
        "current_company": pool_row.get("current_company") or pool_row.get("company") or "",
        "years_experience": years_out,
        "location": pool_row.get("location") or "",
        "industry": pool_row.get("industry") or "",
        "past_companies": pool_row.get("past_companies") or [],
        "past_titles": pool_row.get("past_titles") or [],
        "on_site_note": pool_row.get("note_raw") if flagged else None,
        "note_meaning": note_meaning(pool_row, requirements),
        "unverified": pool_row.get("unverified", False),
        "component_values": {
            name: _exact_or_none(values[name])
            for name in ("skills", "title", "experience", "industry", "notes", "past", "conference")
        },
        "skills_matched": matched,
        "skills_matched_by_meaning": [f"{raw} -> {canonical}" for raw, canonical in semantic],
        "skills_missing": missing,
    }


def apply_llm_briefs(match_rows: list, pool_rows_by_id: dict, requirements: dict,
                      llm_client, shortlist_size: int = 20) -> list:
    """B7 model seam, live path. Runs after score.rank has already produced
    match_rows in final sort order — the top `shortlist_size` rows each get one
    llm.call_candidate_brief call; the rest keep the "" ai_summary/ai_probes
    build_match_row set. Never touches match_score, value_*, points_*, or row
    order — it only fills two columns build_match_row already reserved, and it
    mutates match_rows in place (mirroring score.rank's own convention) as well
    as returning it.

    Called only when main.py was invoked with `match --llm`; without that flag
    this function is never reached and the two columns stay empty (README.md /
    docs/reference/SPEC.md §8: no model call without an explicit opt-in).
    """
    import llm

    job_summary = _build_job_summary(requirements)

    for row in match_rows[:shortlist_size]:
        pool_row = pool_rows_by_id[row["hubspot_id"]]
        candidate = _build_candidate_record(pool_row, requirements, row["_values"])
        brief = llm.call_candidate_brief(llm_client, job_summary, candidate)
        if brief is not None:
            row["ai_summary"] = brief["ai_summary"]
            row["ai_probes"] = brief["ai_probes"]

    return match_rows


def write_matches_csv(rows: list, output_dir) -> Path:
    """B6. output/{job_id}_matches.csv."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    job_id = rows[0]["job_id"] if rows else "UNKNOWN"
    path = output_dir / f"{job_id}_matches.csv"

    csv_rows = [{col: row.get(col, "") for col in MATCHES_COLUMNS} for row in rows]
    pd.DataFrame(csv_rows, columns=MATCHES_COLUMNS).to_csv(path, index=False)
    return path


def _build_shortlist_payload(match_rows: list, pool_rows_by_id: dict, shortlist_size: int) -> list:
    """THE SHORTLIST side of the B7 job-summary prompt: title, company, years,
    industry, matched/missing skills and score for the top `shortlist_size` rows
    — nothing else, per the model-version spec. Reuses '_skills_lists' like
    _build_candidate_record does; no scoring logic re-derived here.
    """
    items = []
    for row in match_rows[:shortlist_size]:
        pool_row = pool_rows_by_id[row["hubspot_id"]]
        matched, _semantic, missing = pool_row["_skills_lists"]
        years = pool_row.get("years_experience")
        years_out = None if years is None or (isinstance(years, float) and math.isnan(years)) else years
        items.append({
            "title": pool_row.get("current_title") or pool_row.get("title") or "",
            "company": pool_row.get("current_company") or pool_row.get("company") or "",
            "years": years_out,
            "industry": pool_row.get("industry") or "",
            "matched": matched,
            "missing": missing,
            "score": row["match_score"],
        })
    return items


def apply_llm_job_summary(match_rows: list, pool_rows_by_id: dict, requirements: dict,
                           llm_client, shortlist_size: int = 20):
    """B7 model seam, shortlist-summary variant. One call for the whole job
    (not per candidate): the top `shortlist_size` rows against the job
    requirements, asking the model to characterise the shortlist as a group.
    Mirrors call_candidate_brief's contract — returns the summary string on a
    well-formed response, None on any failure. There is no deterministic
    fallback for this line (unlike rationale/interview_probes): None here
    means main.py leaves summary_line = "" and write_recruiter_view renders no
    summary line at all — a counted-tiles-read-aloud sentence was judged worse
    than no sentence.
    """
    import llm

    job_summary = _build_job_summary(requirements)
    shortlist = _build_shortlist_payload(match_rows, pool_rows_by_id, shortlist_size)
    return llm.call_shortlist_summary(llm_client, job_summary, shortlist)


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _format_conference_date(date_str) -> str:
    """conference_date is stored 'YYYY-MM-DD' (docs/reference/SPEC.md §A8). The recruiter view
    only ever needs to attribute a quote to a month, so format it once here
    rather than shipping a raw ISO date (or a date-parsing helper) to the browser.
    """
    if not date_str:
        return ""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return date_str


def _edge_to_json(edge: dict) -> dict:
    return {
        "name": edge["employee_name"],
        "title": edge["employee_title"],
        "dept": edge["employee_department"],
        "tier": edge["tier"],
        "shared": edge["shared_employer"] or None,
        "n": int(edge["mutual_count"]),
        "why": referral_why(edge),
    }


def _row_to_data_item(row: dict, pool_row: dict) -> dict:
    """row is a build_match_row() output (carries '_values' and
    '_ordered_edges'); pool_row is that candidate's pool row, carrying
    '_skills_lists' set by main.py before scoring.
    """
    matched, semantic, missing = pool_row["_skills_lists"]
    years = pool_row.get("years_experience")
    years_json = None if years is None or (isinstance(years, float) and math.isnan(years)) else int(years)

    ai_summary = row.get("ai_summary") or ""
    ai_probes = row.get("ai_probes") or ""

    return {
        "n": pool_row["full_name"],
        # Provenance (model vs. template) lives in the CSV's ai_summary column
        # for a reviewer to audit, not on the card — so no "src" field ships
        # to the browser here.
        "why": ai_summary or row.get("rationale", ""),
        "probe": ai_probes or row.get("interview_probes", ""),
        "t": pool_row.get("current_title") or pool_row.get("title") or "",
        "c": pool_row.get("current_company") or pool_row.get("company") or "",
        "y": years_json,
        "l": pool_row.get("location") or "",
        "id": pool_row["hubspot_id"],
        "conf": pool_row.get("conference_name") or "",
        "cd": _format_conference_date(pool_row.get("conference_date")),
        "notes": pool_row.get("note_raw") if pool_row.get("flagged_on_site") else None,
        "meaning": row.get("_note_meaning"),
        # `u`/`b` are emitted only for an unverified row, so a verified one's
        # payload is unchanged. `b` is the component list score_components
        # actually computed (docs/reference/SPEC.md §B3, unverified branch); the recruiter
        # view normalizes over exactly those weights, as score.py does, and
        # labels the rest "not scored" instead of showing them as a zero.
        **({"u": True, "b": row["score_basis"].split(";")} if pool_row.get("unverified") else {}),
        "v": {name: (_exact_or_none(row["_values"][name]) if row["_values"][name] is not None else 0.0)
              for name in ("skills", "title", "experience", "industry", "notes", "past", "conference")},
        "score": row["match_score"],
        "m": matched,
        "s": semantic,
        "x": missing,
        "r": [_edge_to_json(e) for e in row["_ordered_edges"]],
    }


def _index_job_entry(job_row: dict, output_dir: Path) -> dict:
    """One card on the landing page. Reads that job's already-written
    output/<id>_matches.csv rather than re-scoring: the index spans every role
    while `match` runs one at a time, so the committed CSV is the only place
    all four results exist at once. A job with no CSV yet gets href None and
    the card renders the command that produces it.
    """
    entry = {
        "id": job_row["job_id"],
        "title": job_row["title"],
        "department": job_row["department"],
        "seniority": job_row["seniority"],
        "required": [s.strip() for s in job_row.get("required_skills", "").split(";") if s.strip()],
        "href": None,
    }

    matches_path = output_dir / f"{job_row['job_id']}_matches.csv"
    view_path = output_dir / f"{job_row['job_id']}_recruiter_view.html"
    if not matches_path.exists() or not view_path.exists():
        return entry

    with open(matches_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return entry

    # Same three tiles the recruiter view shows, computed the same way: every
    # scored row, those at or above 70, those with at least one usable edge.
    entry["href"] = f"{output_dir.name}/{view_path.name}"
    entry["scored"] = len(rows)
    entry["above70"] = sum(1 for r in rows if int(r["match_score"]) >= 70)
    entry["withReferral"] = sum(1 for r in rows if r["referral_name"])
    entry["topName"] = rows[0]["full_name"]
    entry["topScore"] = int(rows[0]["match_score"])
    return entry


def write_index(jobs_df, pool_size: int, conference_count: int, template_path,
                output_dir, repo_root, fixture_output_dir=None) -> Path:
    """Writes index.html at the repo root: the landing page a reviewer opens
    first. Lists every role in job_openings.csv, links the ones whose report
    already exists, and names the command for the ones that don't.

    Same fill mechanism as write_recruiter_view — the template owns the markup
    and the CSS, and this replaces only the three data literals.
    """
    output_dir, repo_root = Path(output_dir), Path(repo_root)
    jobs = [_index_job_entry(row, output_dir) for row in jobs_df.to_dict(orient="records")]

    fixture = None
    if fixture_output_dir is not None:
        fixture_dir = Path(fixture_output_dir)
        fixture_view = fixture_dir / "JOB001_recruiter_view.html"
        fixture_matches = fixture_dir / "JOB001_matches.csv"
        if fixture_view.exists() and fixture_matches.exists():
            with open(fixture_matches, encoding="utf-8") as f:
                fixture_rows = sum(1 for _ in csv.DictReader(f))
            fixture = {
                "rows": fixture_rows,
                "href": f"{fixture_dir.parent.name}/{fixture_dir.name}/{fixture_view.name}",
            }

    literals = {
        "const JOBS =": json.dumps(jobs, ensure_ascii=False),
        "const POOL =": json.dumps({"size": pool_size, "conferences": conference_count}),
        "const FIXTURE =": json.dumps(fixture, ensure_ascii=False),
    }

    lines = Path(template_path).read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        for prefix, value in literals.items():
            if line.lstrip().startswith(prefix):
                lines[i] = f"{prefix} {value};\n"

    index_path = repo_root / "index.html"
    index_path.write_text("".join(lines), encoding="utf-8")
    return index_path


def build_data_items(match_rows: list, pool_rows_by_id: dict) -> list:
    """Builds the recruiter-view DATA array, in the same rank order as the
    matches CSV, from the same in-memory rows build_match_row produced
    (docs/reference/SPEC.md §B8: "written in the same call from the same in-memory rows").
    """
    return [
        _row_to_data_item(row, pool_rows_by_id[row["hubspot_id"]])
        for row in match_rows
    ]


def write_recruiter_view(data_items: list, job_row, pool_size: int, conference_count: int,
                          weights: dict, template_path, output_dir, summary_line: str = "") -> Path:
    """B6. Copies recruiter_view.html (the approved template), replacing the
    DATA array, DEF weights object, SUMMARY line, and header title/subtitle,
    plus one generated-copy-only JS patch described below. The template file at
    the repo root is only ever read, never written — every change happens on
    the in-memory `text` that becomes the file under output/.

    The JS patch: each DATA item now also carries `score`, the pipeline's
    already-rounded match_score (round_half_up, docs/reference/SPEC.md §B4) — the same
    number the CSV has. Recomputing that number in the browser from `v`
    (component values rounded to 4 decimals for display) can round to a
    different whole percent than the exact pipeline value does, so the
    initial render — default weights, nothing moved yet — displays `score`
    directly instead of calling the recompute function. Moving a weight
    slider is what the live recompute exists for, so it takes over from
    there; the CSV always carries default weights, so this only has to hold
    at the state where they'd otherwise be compared.
    """
    template_path = Path(template_path)
    text = template_path.read_text(encoding="utf-8")

    data_json = json.dumps(data_items, ensure_ascii=False, allow_nan=False)
    def_json = json.dumps(weights, ensure_ascii=False)
    summary_json = json.dumps(summary_line, ensure_ascii=False)

    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("const DATA ="):
            lines[i] = f"const DATA = {data_json};\n"
        elif stripped.startswith("const DEF ="):
            lines[i] = f"const DEF = {def_json};\n"
        elif stripped.startswith("const SUMMARY ="):
            lines[i] = f"const SUMMARY = {summary_json};\n"
    text = "".join(lines)

    original_score_fn = (
        "const score = c => {\n"
        "  const keys = basis(c);\n"
        "  const sum = keys.reduce((a, k) => a + w[k], 0) || 1;\n"
        "  return keys.reduce((a, k) => a + c.v[k] * w[k], 0) / sum * 100;\n"
        "};\n"
    )
    patched_score_fn = (
        "let _weightsChanged = false;\n"
        "const _liveScore = c => {\n"
        "  const keys = basis(c);\n"
        "  const sum = keys.reduce((a, k) => a + w[k], 0) || 1;\n"
        "  return keys.reduce((a, k) => a + c.v[k] * w[k], 0) / sum * 100;\n"
        "};\n"
        "const score = c => _weightsChanged ? _liveScore(c) : c.score;\n"
    )
    if original_score_fn not in text:
        raise ValueError(
            "recruiter_view.html's score() function has changed — update the "
            "generated-copy patch in output.write_recruiter_view to match"
        )
    text = text.replace(original_score_fn, patched_score_fn, 1)

    original_slider_handler = (
        "  el.oninput = () => { w[k] = +el.value; "
        "document.getElementById('o-' + k).textContent = el.value; render(); };\n"
    )
    patched_slider_handler = (
        "  el.oninput = () => { _weightsChanged = true; w[k] = +el.value; "
        "document.getElementById('o-' + k).textContent = el.value; render(); };\n"
    )
    if original_slider_handler not in text:
        raise ValueError(
            "recruiter_view.html's weight-slider handler has changed — update the "
            "generated-copy patch in output.write_recruiter_view to match"
        )
    text = text.replace(original_slider_handler, patched_slider_handler, 1)

    # refHtml() recomputes "why" client-side from r.shared/r.n, duplicating
    # the old shared-employer-or-mutual-count logic in JS. That would go
    # stale against referral_why()'s tier/overlap-aware text now on every
    # edge as r.why, so the screen would contradict the CSV (docs/reference/SPEC.md §4).
    # Patched to read the same string Python already computed.
    original_why = (
        "  const why = r.shared ? `both worked at ${esc(r.shared)}`\n"
        "    : `${r.n} mutual connection${r.n === 1 ? '' : 's'}`;\n"
    )
    patched_why = "  const why = esc(r.why);\n"
    if original_why not in text:
        raise ValueError(
            "recruiter_view.html's refHtml() why computation has changed — update the "
            "generated-copy patch in output.write_recruiter_view to match"
        )
    text = text.replace(original_why, patched_why, 1)

    job_title = job_row["title"]
    subtitle = (
        f"{job_row['job_id']} · {job_row['department']} · {job_row['seniority'].lower()} · "
        f"pool of {pool_size} from {conference_count} conferences"
    )
    text = re.sub(r"<title>.*?</title>", f"<title>{_html_escape('Talent pool — ' + job_title)}</title>", text, count=1)
    text = re.sub(r"<h1>.*?</h1>", f"<h1>{_html_escape(job_title)}</h1>", text, count=1)
    text = re.sub(
        r'(<div class="sub">).*?(</div>)',
        lambda m: m.group(1) + _html_escape(subtitle) + m.group(2),
        text, count=1,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{job_row['job_id']}_recruiter_view.html"
    path.write_text(text, encoding="utf-8")
    return path
