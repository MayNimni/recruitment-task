"""Flow B steps 1-5: parse the job, score the seven components, normalize for
unverified candidates, and rank. Arithmetic only — no network, no clock, no
randomness (ARCHITECTURE.md §1 invariants).

score.py must not import enrich.py or output.py (ARCHITECTURE.md §7: "No
module imports another peer"), so the handful of tiny string/list helpers it
needs are re-declared locally rather than shared.
"""

import math
import re

ALL_COMPONENTS = ["skills", "title", "experience", "industry", "notes", "past", "conference"]
UNVERIFIED_COMPONENTS = ["title", "notes", "conference"]

DEFAULT_WEIGHTS = {
    "skills": 30, "title": 25, "experience": 15, "industry": 13,
    "notes": 10, "past": 5, "conference": 2,
}

SENIORITY_THRESHOLDS = {"Junior": 2, "Mid": 3, "Mid-Senior": 5, "Senior": 6}

TITLE_SENIORITY_TOKENS = {
    "senior", "sr", "staff", "principal", "lead", "head", "of", "junior", "jr",
}
TITLE_BONUS_HIGH = {"senior", "sr", "staff"}
TITLE_BONUS_LOW = {"principal", "lead", "head"}

# Small, local to this module. key_domains phrases in the supplied data don't
# actually contain filler words, but the rule ("minus stopwords") is general.
STOPWORDS = frozenset({"a", "an", "the", "and", "or", "of", "in", "on", "for", "with", "to", "from"})

INDUSTRY_SPORTS_TOKEN = "sport"
INDUSTRY_MEDIA_TOKENS = ("video", "broadcast", "streaming", "media", "ott")


def split_list(raw) -> list:
    if raw is None:
        return []
    raw = str(raw).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def tokenize_words(text) -> list:
    """Lowercase, replace non-alphanumerics with spaces, split."""
    text = "" if text is None else str(text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def clean_title(title: str) -> str:
    """Part before ' - ', lowercase, seniority tokens removed (SPEC.md §B3)."""
    title = "" if title is None else str(title)
    head = title.split(" - ")[0]
    words = [w for w in tokenize_words(head) if w not in TITLE_SENIORITY_TOKENS]
    return " ".join(words)


def parse_alias_hits(raw) -> list:
    """Deserializes a pool row's skills_alias_hits ('RAW -> CANONICAL' entries,
    already computed once in Flow A/A3) into (raw, canonical) tuples.
    """
    pairs = []
    for entry in split_list(raw) if isinstance(raw, str) else (raw or []):
        if " -> " in entry:
            raw_skill, canonical = entry.split(" -> ", 1)
            pairs.append((raw_skill.strip(), canonical.strip()))
    return pairs


def normalize_job_skill_list(raw_skills, aliases: dict) -> list:
    """Same 3-step resolution as enrich.normalize_skills (exact case-insensitive
    match on the alias table's own canonical values, then the alias table, then
    passthrough), applied here to a job's required_skills/nice_to_have instead
    of a candidate's top_skills. Duplicated rather than imported — score.py
    may not import enrich.py (ARCHITECTURE.md §7).
    """
    tokens = raw_skills if isinstance(raw_skills, list) else split_list(raw_skills)
    canonical_by_lower = {}
    for canonical in aliases.values():
        canonical_by_lower.setdefault(canonical.lower(), canonical)

    resolved = []
    for token in tokens:
        lower = token.lower()
        if lower in canonical_by_lower:
            resolved.append(canonical_by_lower[lower])
        elif lower in aliases:
            resolved.append(aliases[lower])
        else:
            resolved.append(token)
    return resolved


def build_domain_vocabulary(key_domains_raw, aliases: dict) -> set:
    """key_domains split on ';', tokenized into words, plus the alias-table
    expansion of any phrase or word that is itself an alias key, minus
    stopwords (SPEC.md §B1).
    """
    canonical_by_lower = {c.lower() for c in aliases.values()}
    vocabulary = set()

    for phrase in split_list(key_domains_raw):
        words = tokenize_words(phrase)
        vocabulary.update(w for w in words if w not in STOPWORDS)

        candidates = [phrase.strip().lower(), *words]
        for candidate in candidates:
            if candidate in canonical_by_lower:
                continue
            canonical = aliases.get(candidate)
            if canonical:
                vocabulary.update(w for w in tokenize_words(canonical) if w not in STOPWORDS)

    return vocabulary


def parse_job(job_row, aliases: dict, title_families: dict, company_domains: dict) -> dict:
    """B1. Builds the full requirement set the seven components read.

    SPEC.md's table lists this as parse_job(job_row, aliases); it takes two
    more config dicts here for the same reason A4's extract_note_tags needed
    `aliases` beyond its own table listing — the components need a complete,
    self-contained requirement bundle (title family for this job's
    department, sports/media keyword lists) and Flow B has no other place to
    attach them without breaking the "no peer imports" rule.

    Deterministic fallback, same seam pattern as resolve_unknown_alias. In production this
    function receives a job description as prose rather than three tidy CSV columns, and a
    language model parses it into this same requirement set. It is deterministic here so the
    pipeline runs with no API key and returns identical output on every run.
    """
    department = job_row["department"]
    seniority = job_row["seniority"]
    if seniority not in SENIORITY_THRESHOLDS:
        raise ValueError(f"unknown seniority '{seniority}' for job {job_row.get('job_id')}")

    required_skills = normalize_job_skill_list(job_row.get("required_skills", ""), aliases)
    nice_to_have = normalize_job_skill_list(job_row.get("nice_to_have", ""), aliases)

    return {
        "job_id": job_row["job_id"],
        "job_title": job_row["title"],
        "department": department,
        "seniority": seniority,
        "seniority_threshold": SENIORITY_THRESHOLDS[seniority],
        "required_skills": required_skills,
        "nice_to_have": nice_to_have,
        "key_domains": split_list(job_row.get("key_domains", "")),
        "domain_vocabulary": build_domain_vocabulary(job_row.get("key_domains", ""), aliases),
        "title_family": title_families.get(department, []),
        "sports_keywords": company_domains.get("sports_keywords", []),
        "media_keywords": company_domains.get("media_keywords", []),
    }


def _is_missing_years(years) -> bool:
    return years is None or (isinstance(years, float) and math.isnan(years))


def round_half_up(x: float) -> int:
    # Matches Math.round in the recruiter view template (round-half-up), unlike Python's
    # built-in round() which rounds half-to-even and would disagree with the HTML on any
    # score ending in exactly .5.
    return math.floor(x + 0.5)


def score_skills(pool_row: dict, requirements: dict) -> float:
    """Weight 30. matched / len(required_skills)."""
    required = requirements["required_skills"]
    if not required:
        return 0.0
    canonical = set(pool_row.get("skills_canonical") or [])
    matched = sum(1 for skill in required if skill in canonical)
    return matched / len(required)


def skills_lists(pool_row: dict, requirements: dict):
    """Companion to score_skills: the three lists the CSV/HTML need.
    skills_matched: required skills present in skills_canonical (their own
    literal name, whichever path resolved them). skills_semantic: the subset
    of those resolved via the alias table, as [raw, canonical] pairs, read
    from the candidate's own skills_alias_hits (computed once in A3 — Flow B
    does not re-derive it). skills_missing: required skills absent.
    """
    required = requirements["required_skills"]
    canonical = set(pool_row.get("skills_canonical") or [])
    alias_pairs = parse_alias_hits(pool_row.get("skills_alias_hits"))
    raw_by_canonical = {c: r for r, c in alias_pairs}

    matched, semantic, missing = [], [], []
    for skill in required:
        if skill in canonical:
            matched.append(skill)
            if skill in raw_by_canonical:
                semantic.append([raw_by_canonical[skill], skill])
        else:
            missing.append(skill)
    return matched, semantic, missing


def score_title(pool_row: dict, requirements: dict) -> float:
    """Weight 25. min(1.0, core + seniority_bonus). Uses the candidate's
    self-reported `title`, not `current_title` — the former is always present
    (even when unverified), and title is one of the three components an
    unverified candidate can still be scored on (SPEC.md §B3).
    """
    original_title = pool_row.get("title") or ""
    cleaned = clean_title(original_title)
    cleaned_words = set(cleaned.split())

    core = 0.0
    if any(cleaned.endswith(family) for family in requirements["title_family"]):
        core = 0.7
    elif cleaned_words & requirements["domain_vocabulary"]:
        core = 0.25

    title_words = set(tokenize_words(original_title))
    if title_words & TITLE_BONUS_HIGH:
        bonus = 0.3
    elif title_words & TITLE_BONUS_LOW:
        bonus = 0.15
    else:
        bonus = 0.0

    return min(1.0, core + bonus)


def score_experience(pool_row: dict, requirements: dict) -> float:
    """Weight 15. 1.0 at/above threshold, else years / threshold."""
    years = pool_row.get("years_experience")
    if _is_missing_years(years):
        return 0.0
    threshold = requirements["seniority_threshold"]
    years = float(years)
    if years >= threshold:
        return 1.0
    return years / threshold


def score_industry(pool_row: dict, requirements: dict) -> float:
    """Weight 13. Lowercased substring match on the industry field."""
    industry = str(pool_row.get("industry") or "").lower()
    if INDUSTRY_SPORTS_TOKEN in industry:
        return 1.0
    if any(token in industry for token in INDUSTRY_MEDIA_TOKENS):
        return 0.5
    return 0.0


def score_notes(pool_row: dict, requirements: dict) -> float:
    """Weight 10. 0 if no note, 0.5 if a note exists with no domain overlap,
    1.0 if note_tags intersects domain_vocabulary.
    """
    if not pool_row.get("flagged_on_site"):
        return 0.0
    tags = set(pool_row.get("note_tags") or [])
    return 1.0 if tags & requirements["domain_vocabulary"] else 0.5


def score_past_companies(pool_row: dict, requirements: dict) -> float:
    """Weight 5. Same two keyword lists as score_industry, read from
    company_domains.json, applied per past_companies entry, best across all.
    """
    entries = pool_row.get("past_companies") or []
    if not entries:
        return 0.0
    sports_kw = requirements["sports_keywords"]
    media_kw = requirements["media_keywords"]
    best = 0.0
    for entry in entries:
        low = entry.lower()
        if any(kw in low for kw in sports_kw):
            best = max(best, 1.0)
        elif any(kw in low for kw in media_kw):
            best = max(best, 0.5)
    return best


def score_conference(pool_row: dict, requirements: dict) -> float:
    """Weight 2. 1.0 if conference_domain tokens intersect domain_vocabulary."""
    tokens = set(tokenize_words(pool_row.get("conference_domain")))
    return 1.0 if tokens & requirements["domain_vocabulary"] else 0.0


COMPONENT_FUNCS = {
    "skills": score_skills,
    "title": score_title,
    "experience": score_experience,
    "industry": score_industry,
    "notes": score_notes,
    "past": score_past_companies,
    "conference": score_conference,
}


def score_components(pool_row: dict, requirements: dict):
    """B3 + B4-normalize. Computes all seven components, unless the candidate
    is unverified, in which case only title/notes/conference are computed
    (SPEC.md §B3) and the rest are None — never 0, which would read as poor
    fit rather than unknown (DECISIONS.md §2.2/§4.2).

    Returns (values: dict[str, float|None], score_basis: list[str]).
    """
    basis = UNVERIFIED_COMPONENTS if pool_row.get("unverified") else ALL_COMPONENTS
    values = {
        name: (COMPONENT_FUNCS[name](pool_row, requirements) if name in basis else None)
        for name in ALL_COMPONENTS
    }
    return values, basis


def rank(scored_rows: list, weights: dict = DEFAULT_WEIGHTS) -> list:
    """B4. Each scored_row must already carry 'values', 'score_basis',
    'hubspot_id', 'years_experience'. Adds 'match_score' and 'points' (a
    dict paralleling 'values', each entry = value * weight, None where the
    value is None) to every row, in place, and returns the rows sorted
    descending by match_score, ties broken by years_experience (descending,
    missing treated as lowest) then hubspot_id (ascending).

    match_score = round_half_up(sum(value * weight for computed components) /
    sum(those weights) * 100). For a fully-computed (verified) row the
    weights already sum to 100, so points_x = value * weight is directly
    comparable to match_score with no further rescaling — that rescaling
    only does anything for the unverified case, where fewer weights are summed.
    """
    for row in scored_rows:
        values = row["values"]
        basis = row["score_basis"]
        weight_sum = sum(weights[name] for name in basis)
        weighted_sum = sum(values[name] * weights[name] for name in basis)
        row["match_score"] = round_half_up(weighted_sum / weight_sum * 100) if weight_sum else 0
        row["points"] = {
            name: (values[name] * weights[name] if values[name] is not None else None)
            for name in ALL_COMPONENTS
        }

    def sort_key(row):
        years = row.get("years_experience")
        years_key = -1.0 if _is_missing_years(years) else float(years)
        return (-row["match_score"], -years_key, row["hubspot_id"])

    return sorted(scored_rows, key=sort_key)
