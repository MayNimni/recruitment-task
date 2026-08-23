"""Flow A steps 2-8: join, skill normalization, note tagging, referral edges,
tiering, and the pool write. Every function here is job-independent — none of
them may read job_openings.csv (ARCHITECTURE.md §1 invariants).

List-valued fields move through this module as Python lists and are only
joined into ';'-strings at the very end, in build_pool / write_pool.
"""

import re
from pathlib import Path

import pandas as pd

# A6 — generic tokens dropped before comparing former employers (SPEC.md §A6).
# Written in their natural (unstemmed) form; stem_token() is applied to this
# set once, below, before it's used for lookups.
GENERIC_COMPANY_TOKENS = frozenset({
    "technologies", "software", "group", "sports", "lab", "labs", "research",
    "unit", "freelance", "startup", "inc", "ltd", "media",
    "digital", "online", "global", "network", "solution", "service", "system",
    "international", "company", "holding", "studio", "partner", "venture",
    "consulting", "agency", "technology",
})


def stem_token(token: str) -> str:
    """Strip one trailing plural 's' from tokens longer than four characters.

    A plural strip, not a lemmatizer: 'technologies' -> 'technologie', not
    'technology'. Applied identically to company-name tokens and to the
    drop-list itself, so 'sports' on the drop-list and 'sport' in a company
    name resolve to the same token (SPEC.md §A6).
    """
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


GENERIC_COMPANY_TOKENS_STEMMED = frozenset(
    stem_token(word) for word in GENERIC_COMPANY_TOKENS
)

# A4 — standard filler words, plus a few scene-setting verbs specific to the
# on-site note style ("spoke about", "mentioned"), stripped before tagging.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "for", "with", "to",
    "from", "about", "very", "this", "that", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "as", "by", "into", "over", "across", "per",
    "spoke", "mentioned", "published", "team", "yrs", "years",
})

POOL_COLUMNS = [
    "hubspot_id", "full_name", "email", "company", "title", "linkedin_url",
    "current_company", "current_title", "location", "years_experience",
    "industry", "past_companies", "past_titles", "skills_raw",
    "skills_canonical", "skills_alias_hits", "note_raw", "note_tags",
    "flagged_on_site", "unverified", "conference_name", "conference_domain",
    "conference_date", "source", "first_seen_at", "last_refreshed_at",
    "ats_status", "pool_status",
]

EDGE_COLUMNS = [
    "hubspot_id", "employee_id", "employee_name", "employee_title",
    "employee_department", "mutual_count", "shared_employer", "tier",
    "referral_feedback",
]


def split_list(raw) -> list:
    """';'-separated string -> list of stripped, non-empty items."""
    if raw is None:
        return []
    raw = str(raw).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def join_list(items) -> str:
    return ";".join(items)


def normalize_linkedin_url(url) -> str:
    """Strip whitespace and a leading scheme or 'www.' (SPEC.md §A2)."""
    if url is None:
        return ""
    u = str(url).strip()
    u = re.sub(r"^https?://", "", u, flags=re.IGNORECASE)
    u = re.sub(r"^www\.", "", u, flags=re.IGNORECASE)
    return u.strip()


def join_profiles(attendees: pd.DataFrame, profiles: pd.DataFrame) -> pd.DataFrame:
    """A2. Left join attendees to profiles on normalized linkedin_url.

    A row with no match is kept: unverified=True and every profile-derived
    column is ''. No name-based fallback (DECISIONS.md §4.2 / §2.2).
    """
    left = attendees.copy()
    right = profiles.copy()
    left["_join_key"] = left["linkedin_url"].map(normalize_linkedin_url)
    right["_join_key"] = right["linkedin_url"].map(normalize_linkedin_url)

    # Drop profile columns that would collide with the attendee's own copy;
    # the attendee's self-reported linkedin_url/full_name are what the pool keeps.
    right = right.drop(columns=["linkedin_url", "full_name"])

    merged = left.merge(right, on="_join_key", how="left", indicator=True)
    merged["unverified"] = merged["_merge"] == "left_only"
    merged = merged.drop(columns=["_merge", "_join_key"])

    profile_string_cols = [
        "current_company", "current_title", "location", "industry",
        "past_companies", "past_titles", "top_skills", "wsc_mutual_connections",
    ]
    for col in profile_string_cols:
        if col in merged.columns:
            merged[col] = merged[col].fillna("")

    return merged


def resolve_unknown_alias(token: str) -> str:
    """Deterministic fallback. In production this function calls a language model to map an
    unseen skill name onto the canonical set, caches the result back into
    `skill_aliases.json`, and the model is never called twice for the same token. It is
    deterministic here so the pipeline runs with no API key and returns identical output on
    every run.
    """
    return token


def normalize_skills(raw_skills, aliases: dict):
    """A3. raw_skills is the ';'-separated top_skills string (or an already-split list).

    Resolution order: exact case-insensitive match on the canonical set (the
    alias table's own values), then the alias table, then resolve_unknown_alias.
    Returns (skills_raw, skills_canonical, skills_alias_hits).
    """
    tokens = raw_skills if isinstance(raw_skills, list) else split_list(raw_skills)

    canonical_by_lower = {}
    for canonical in aliases.values():
        canonical_by_lower.setdefault(canonical.lower(), canonical)

    skills_canonical = []
    skills_alias_hits = []
    for token in tokens:
        lower = token.lower()
        if lower in canonical_by_lower:
            skills_canonical.append(canonical_by_lower[lower])
        elif lower in aliases:
            resolved = aliases[lower]
            skills_canonical.append(resolved)
            skills_alias_hits.append(f"{token} -> {resolved}")
        else:
            skills_canonical.append(resolve_unknown_alias(token))

    return tokens, skills_canonical, skills_alias_hits


def extract_note_tags(note, aliases: dict):
    """A4. Lowercase, strip punctuation and stopwords, keep the remaining
    tokens plus their canonical forms via the alias table.

    Same seam pattern as resolve_unknown_alias: in production this extractor
    is replaced by a model returning structured signal tags (DECISIONS.md §6.1
    / ARCHITECTURE.md §9.2). Note *value* is not computed here — that is B3.
    """
    note = "" if note is None else str(note)
    flagged_on_site = bool(note.strip())

    text = re.sub(r"[^a-z0-9\s]", " ", note.lower())
    words = [
        w for w in text.split()
        if w not in STOPWORDS and len(w) >= 2 and not w.isdigit()
    ]

    tags = []
    seen_lower = set()
    for word in words:
        if word not in seen_lower:
            tags.append(word)
            seen_lower.add(word)
        canonical = aliases.get(word)
        if canonical and canonical.lower() not in seen_lower:
            tags.append(canonical)
            seen_lower.add(canonical.lower())

    return tags, flagged_on_site


def resolve_mutual_connections(row, employees_by_id: pd.DataFrame):
    """A5. One edge per listed employee id; mutual_count is identical across
    every edge produced for this candidate. Unknown ids are skipped rather
    than fabricated into an edge.
    """
    ids = split_list(row.get("wsc_mutual_connections", ""))
    mutual_count = len(ids)

    edges = []
    for employee_id in ids:
        if employee_id not in employees_by_id.index:
            continue
        emp = employees_by_id.loc[employee_id]
        edges.append({
            "hubspot_id": row["hubspot_id"],
            "employee_id": employee_id,
            "employee_name": emp["full_name"],
            "employee_title": emp["title"],
            "employee_department": emp["department"],
            "mutual_count": mutual_count,
            "shared_employer": "",
        })
    return edges, mutual_count


def company_tokens(entry: str, generic_tokens=GENERIC_COMPANY_TOKENS_STEMMED) -> set:
    """Strip a trailing (YYYY-YYYY) date range, lowercase, split on whitespace,
    stem each word (stem_token), drop generic tokens, keep tokens of length
    >= 4. Shared by both sides of the A6 comparison. Whole-token equality
    only — never substring matching, which is what would incorrectly pair a
    candidate from Intel with an employee from an IDF Intelligence Unit
    (SPEC.md §A6, DECISIONS.md §4.3/§2.3).
    """
    text = re.sub(r"\([^)]*\)", "", entry).lower()
    tokens = set()
    for word in text.split():
        stemmed = stem_token(word)
        if stemmed in generic_tokens or len(stemmed) < 4:
            continue
        tokens.add(stemmed)
    return tokens


def employee_company_tokens(work_history: str, generic_tokens=GENERIC_COMPANY_TOKENS_STEMMED) -> set:
    tokens = set()
    for entry in split_list(work_history):
        tokens |= company_tokens(entry, generic_tokens)
    return tokens


def find_shared_employers(row, employees: pd.DataFrame, generic_tokens=GENERIC_COMPANY_TOKENS_STEMMED):
    """A6. Yields (employee_id, matched_company_name) for every employee whose
    work_history shares a non-generic token of length >= 4 with one of the
    candidate's past_companies entries. May match candidates with mutual_count
    of 0 — that is the point of this step (SPEC.md §A6).
    """
    candidate_token_sets = [
        (entry, company_tokens(entry, generic_tokens))
        for entry in split_list(row.get("past_companies", ""))
    ]

    matches = []
    for _, emp in employees.iterrows():
        emp_tokens = employee_company_tokens(emp["work_history"], generic_tokens)
        if not emp_tokens:
            continue
        for entry, cand_tokens in candidate_token_sets:
            if cand_tokens & emp_tokens:
                matches.append((emp["employee_id"], entry))
                break
    return matches


def assign_tier(edge: dict):
    """A7. A = shared employer AND mutual_count>=1. B = no shared employer,
    mutual_count>=3. C = shared employer AND mutual_count==0. D = no shared
    employer, mutual_count in {1,2}. Shared employment corroborates a
    connection rather than substituting for one (SPEC.md §A7,
    DECISIONS.md §2.3) — a shared employer with zero mutual connections is
    weaker evidence than a shared employer confirmed by at least one mutual
    connection. Every edge that exists carries one of these four tiers; a
    candidate with no edge at all simply has no row, so this only returns
    None for a shared_employer/mutual_count combination that resolve_mutual_connections
    and find_shared_employers never actually produce.
    """
    shared = bool(edge.get("shared_employer"))
    count = edge.get("mutual_count", 0)
    if shared and count >= 1:
        return "A"
    if not shared and count >= 3:
        return "B"
    if shared and count == 0:
        return "C"
    if not shared and count in (1, 2):
        return "D"
    return None


def build_pool(sources: dict):
    """Runs A2 through A7 over every attendee and returns (pool_df, edges_df),
    still unwritten. write_pool (A8) serializes them.
    """
    attendees = sources["attendees"]
    profiles = sources["profiles"]
    employees = sources["employees"]
    aliases = sources["skill_aliases"]

    merged = join_profiles(attendees, profiles)
    employees_by_id = employees.set_index("employee_id", drop=False)

    pool_rows = []
    edges_by_key = {}
    edges_order = []

    for _, row in merged.iterrows():
        skills_raw, skills_canonical, skills_alias_hits = normalize_skills(
            row.get("top_skills", ""), aliases
        )
        note_tags, flagged_on_site = extract_note_tags(row.get("notes", ""), aliases)

        pool_rows.append({
            "hubspot_id": row["hubspot_id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "company": row["company"],
            "title": row["title"],
            "linkedin_url": row["linkedin_url"],
            "current_company": row.get("current_company", ""),
            "current_title": row.get("current_title", ""),
            "location": row.get("location", ""),
            "years_experience": row.get("years_experience", pd.NA),
            "industry": row.get("industry", ""),
            "past_companies": join_list(split_list(row.get("past_companies", ""))),
            "past_titles": join_list(split_list(row.get("past_titles", ""))),
            "skills_raw": join_list(skills_raw),
            "skills_canonical": join_list(skills_canonical),
            "skills_alias_hits": join_list(skills_alias_hits),
            "note_raw": row.get("notes", ""),
            "note_tags": join_list(note_tags),
            "flagged_on_site": flagged_on_site,
            "unverified": row["unverified"],
            "conference_name": row["conference_name"],
            "conference_domain": row["conference_domain"],
            "conference_date": row["conference_date"],
            "source": row["source"],
            # No clock is available (or allowed) in Flow A; the conference date
            # is the only trustworthy timestamp the source data carries.
            "first_seen_at": row["conference_date"],
            "last_refreshed_at": row["conference_date"],
            "ats_status": "",
            "pool_status": "active",
        })

        edges, _mutual_count = resolve_mutual_connections(row, employees_by_id)
        for edge in edges:
            key = (edge["hubspot_id"], edge["employee_id"])
            edges_by_key[key] = edge
            edges_order.append(key)

        for employee_id, matched_company in find_shared_employers(row, employees):
            key = (row["hubspot_id"], employee_id)
            if key in edges_by_key:
                if not edges_by_key[key]["shared_employer"]:
                    edges_by_key[key]["shared_employer"] = matched_company
            else:
                emp = employees_by_id.loc[employee_id]
                new_edge = {
                    "hubspot_id": row["hubspot_id"],
                    "employee_id": employee_id,
                    "employee_name": emp["full_name"],
                    "employee_title": emp["title"],
                    "employee_department": emp["department"],
                    "mutual_count": 0,
                    "shared_employer": matched_company,
                }
                edges_by_key[key] = new_edge
                edges_order.append(key)

    edge_rows = []
    for key in edges_order:
        edge = edges_by_key[key]
        edge["tier"] = assign_tier(edge)
        edge["referral_feedback"] = "not_requested"
        edge_rows.append(edge)

    pool_df = pd.DataFrame(pool_rows, columns=POOL_COLUMNS)
    pool_df = pool_df.sort_values("hubspot_id", kind="stable").reset_index(drop=True)

    edges_df = pd.DataFrame(edge_rows, columns=EDGE_COLUMNS)
    if not edges_df.empty:
        edges_df = edges_df.sort_values(
            ["hubspot_id", "employee_id"], kind="stable"
        ).reset_index(drop=True)

    return pool_df, edges_df


def write_pool(pool_df: pd.DataFrame, edges_df: pd.DataFrame, pool_dir):
    """A8. Writes pool/talent_pool.csv and pool/referral_edges.csv."""
    pool_dir = Path(pool_dir)
    pool_dir.mkdir(parents=True, exist_ok=True)

    pool_path = pool_dir / "talent_pool.csv"
    edges_path = pool_dir / "referral_edges.csv"

    pool_df.to_csv(pool_path, index=False)
    edges_df.to_csv(edges_path, index=False)

    return pool_path, edges_path
