"""Flow B steps 5-8: referral selection, rationale/probe templates, and the
two output writers. output.py must not import score.py or enrich.py
(ARCHITECTURE.md §7) — everything it needs arrives as plain data from main.py.
"""

import json
import math
import re
from pathlib import Path

import pandas as pd

TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}

# B5 department-distance map. Not specified anywhere in the docs beyond "a
# small explicit map" (SPEC.md §B5) naming same-department=0 and a named
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
    "interview_probes", "referral_name", "referral_title",
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
    """'both worked at X' for tiers A and C (both carry a shared employer),
    'N mutual connections' otherwise."""
    if edge.get("shared_employer"):
        return f"both worked at {edge['shared_employer']}"
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
    if missing:
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


def _round_or_none(value, digits=4):
    """Component values/points are exact fractions (e.g. 5/6) that don't
    terminate in binary floating point (0.8333333333333334). Rounding here is
    presentation only — the underlying math in score.py is untouched — so the
    CSV and recruiter view stay traceable without carrying float noise past
    what a recruiter (or a `value * weight` check) would ever look at.
    """
    return None if value is None else round(value, digits)


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
        row[f"value_{name}"] = _blank_if_none(_round_or_none(values[name]))
        row[f"points_{name}"] = _blank_if_none(_round_or_none(points[name]))

    # Consumed by write_recruiter_view, dropped before the CSV write (MATCHES_COLUMNS
    # doesn't include them, so building the CSV frame from that fixed column list ignores them).
    row["_ordered_edges"] = ordered_edges
    row["_values"] = values
    return row


def write_matches_csv(rows: list, output_dir) -> Path:
    """B6. output/{job_id}_matches.csv."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    job_id = rows[0]["job_id"] if rows else "UNKNOWN"
    path = output_dir / f"{job_id}_matches.csv"

    csv_rows = [{col: row.get(col, "") for col in MATCHES_COLUMNS} for row in rows]
    pd.DataFrame(csv_rows, columns=MATCHES_COLUMNS).to_csv(path, index=False)
    return path


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _edge_to_json(edge: dict) -> dict:
    return {
        "name": edge["employee_name"],
        "title": edge["employee_title"],
        "dept": edge["employee_department"],
        "tier": edge["tier"],
        "shared": edge["shared_employer"] or None,
        "n": int(edge["mutual_count"]),
    }


def _row_to_data_item(row: dict, pool_row: dict) -> dict:
    """row is a build_match_row() output (carries '_values' and
    '_ordered_edges'); pool_row is that candidate's pool row, carrying
    '_skills_lists' set by main.py before scoring.
    """
    matched, semantic, missing = pool_row["_skills_lists"]
    years = pool_row.get("years_experience")
    years_json = None if years is None or (isinstance(years, float) and math.isnan(years)) else int(years)

    return {
        "n": pool_row["full_name"],
        "t": pool_row.get("current_title") or pool_row.get("title") or "",
        "c": pool_row.get("current_company") or pool_row.get("company") or "",
        "y": years_json,
        "l": pool_row.get("location") or "",
        "id": pool_row["hubspot_id"],
        "conf": pool_row.get("conference_name") or "",
        "notes": pool_row.get("note_raw") if pool_row.get("flagged_on_site") else None,
        "v": {name: (_round_or_none(row["_values"][name]) if row["_values"][name] is not None else 0.0)
              for name in ("skills", "title", "experience", "industry", "notes", "past", "conference")},
        "score": row["match_score"],
        "m": matched,
        "s": semantic,
        "x": missing,
        "r": [_edge_to_json(e) for e in row["_ordered_edges"]],
    }


def build_data_items(match_rows: list, pool_rows_by_id: dict) -> list:
    """Builds the recruiter-view DATA array, in the same rank order as the
    matches CSV, from the same in-memory rows build_match_row produced
    (SPEC.md §B6: "written in the same call from the same in-memory rows").
    """
    return [
        _row_to_data_item(row, pool_rows_by_id[row["hubspot_id"]])
        for row in match_rows
    ]


def write_recruiter_view(data_items: list, job_row, pool_size: int, conference_count: int,
                          weights: dict, template_path, output_dir) -> Path:
    """B6. Copies recruiter_view.html (the approved template), replacing the
    DATA array, DEF weights object, and header title/subtitle, plus one
    generated-copy-only JS patch described below. The template file at the
    repo root is only ever read, never written — every change happens on the
    in-memory `text` that becomes the file under output/.

    The JS patch: each DATA item now also carries `score`, the pipeline's
    already-rounded match_score (round_half_up, SPEC.md §B4) — the same
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

    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("const DATA ="):
            lines[i] = f"const DATA = {data_json};\n"
        elif stripped.startswith("const DEF ="):
            lines[i] = f"const DEF = {def_json};\n"
    text = "".join(lines)

    original_score_fn = (
        "const score = c => {\n"
        "  const sum = KEYS.reduce((a, k) => a + w[k], 0) || 1;\n"
        "  return KEYS.reduce((a, k) => a + c.v[k] * w[k], 0) / sum * 100;\n"
        "};\n"
    )
    patched_score_fn = (
        "let _weightsChanged = false;\n"
        "const _liveScore = c => {\n"
        "  const sum = KEYS.reduce((a, k) => a + w[k], 0) || 1;\n"
        "  return KEYS.reduce((a, k) => a + c.v[k] * w[k], 0) / sum * 100;\n"
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
