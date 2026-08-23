"""Build-time generator for data/skill_aliases.json.

Standalone script, run manually as `python pipeline/build_aliases.py`. It is NOT imported
by main.py and is never part of the ingest or match run — the pipeline itself never calls
a model; only this offline generator does.

It reads every skill mentioned on the candidate side (linkedin_profiles.csv top_skills) and
the job side (job_openings.csv required_skills / nice_to_have), treats the job side as the
canonical vocabulary, and asks Claude to map candidate-side skill spellings that mean the
same thing onto that vocabulary. The result is merged into the existing hand-written
data/skill_aliases.json (existing entries win) and written back sorted by key.

Mapping direction rule: an alias may only map onto a canonical skill it unambiguously implies
(a narrow, specific term pointing to the broader skill it's an instance of — e.g. YOLO ->
Object Detection, OpenCV -> Computer Vision). Never the reverse: a broad domain or a specific
product must not be mapped onto a narrower/different specific skill (e.g. "deep learning" ->
"PyTorch" and "postgresql" -> "SQL" are both wrong and were removed for this reason).
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_FILE = DATA_DIR / "linkedin_profiles.csv"
JOBS_FILE = DATA_DIR / "job_openings.csv"
ALIASES_FILE = DATA_DIR / "skill_aliases.json"

MODEL = "claude-sonnet-4-6"


def split_skills(series: pd.Series) -> set[str]:
    skills = set()
    for cell in series:
        if not cell:
            continue
        for token in str(cell).split(";"):
            token = token.strip()
            if token:
                skills.add(token)
    return skills


def load_vocab_and_candidates() -> tuple[set[str], set[str]]:
    profiles = pd.read_csv(PROFILES_FILE, dtype=str, keep_default_na=False)
    jobs = pd.read_csv(JOBS_FILE, dtype=str, keep_default_na=False)

    candidate_skills = split_skills(profiles["top_skills"])
    canonical_skills = split_skills(jobs["required_skills"]) | split_skills(jobs["nice_to_have"])

    return canonical_skills, candidate_skills


def build_prompt(canonical_skills: set[str], candidate_skills: set[str]) -> str:
    canonical_list = sorted(canonical_skills)
    alias_candidates = sorted(candidate_skills - canonical_skills)

    return f"""You are building a skill-alias dictionary for a recruiting matching pipeline.

Canonical vocabulary (the exact skill names used by job postings):
{json.dumps(canonical_list, indent=2)}

Candidate skill spellings that need to be mapped onto the canonical vocabulary (these come from
LinkedIn profiles and may use different casing, abbreviations, synonyms, or related terminology):
{json.dumps(alias_candidates, indent=2)}

For each candidate skill spelling that means the same thing as (or is a direct synonym/abbreviation
of) one of the canonical skills, map it to that canonical skill. Omit any candidate skill that does
not clearly map onto one of the canonical skills — do not invent a mapping or guess at a loose
association.

Mapping direction is one-directional and strict: only map a narrow, unambiguous term onto the
broader canonical skill it always implies (e.g. YOLO -> Object Detection, OpenCV -> Computer
Vision — YOLO and OpenCV are specific things that ARE ALWAYS an instance of the canonical skill).
Never map a broad domain or a distinct specific product onto a narrower or merely-related
canonical skill. For example, "deep learning" must NOT map to "PyTorch" (deep learning is a
broad field; PyTorch is just one framework used within it, not a synonym), and "postgresql" must
NOT map to "SQL" (PostgreSQL is a specific database product, not a rephrasing of the general
concept "SQL"). If you are unsure which direction is narrower, omit the mapping rather than guess.

Respond with ONLY a JSON object (no markdown fences, no commentary) where:
- each key is the candidate skill spelling, lowercased
- each value is the exact canonical skill name (must match one of the canonical vocabulary entries
  exactly, including casing)

Example shape: {{"ml": "Machine Learning", "cv": "Computer Vision"}}
"""


def call_model(prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print(
            "ERROR: the 'anthropic' package is not installed. Install it with:\n"
            "  pip3 install anthropic --break-system-packages",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def parse_model_response(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def validate_mapping(mapping: dict, canonical_skills: set[str]) -> tuple[dict, list[str]]:
    """Returns (accepted, rejection_reasons)."""
    if not isinstance(mapping, dict):
        return {}, [f"model response was not a JSON object: {type(mapping).__name__}"]

    accepted = {}
    rejected = []
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, str):
            rejected.append(f"{key!r} -> {value!r}: key/value must both be strings")
            continue
        lower_key = key.lower()
        if value not in canonical_skills:
            rejected.append(f"{key!r} -> {value!r}: value is not in the canonical vocabulary")
            continue
        if lower_key == value.lower():
            rejected.append(f"{key!r} -> {value!r}: key maps to itself")
            continue
        accepted[lower_key] = value

    return accepted, rejected


def merge_aliases(existing: dict, generated: dict) -> tuple[dict, list[str], list[str]]:
    """Existing entries win on conflict. Returns (merged, added, conflicts)."""
    merged = dict(existing)
    added = []
    conflicts = []

    for key, value in generated.items():
        if key in existing:
            if existing[key] != value:
                conflicts.append(f"{key!r}: kept existing {existing[key]!r}, model suggested {value!r}")
        else:
            merged[key] = value
            added.append(f"{key!r} -> {value!r}")

    return merged, added, conflicts


def main():
    if ALIASES_FILE.exists():
        with open(ALIASES_FILE, encoding="utf-8") as f:
            existing_aliases = json.load(f)
    else:
        existing_aliases = {}

    canonical_skills, candidate_skills = load_vocab_and_candidates()

    prompt = build_prompt(canonical_skills, candidate_skills)
    print("=" * 80)
    print("PROMPT SENT TO MODEL:")
    print("=" * 80)
    print(prompt)
    print("=" * 80)

    raw_response = call_model(prompt)

    try:
        raw_mapping = parse_model_response(raw_response)
    except json.JSONDecodeError as e:
        print(f"ERROR: model response was not valid JSON: {e}", file=sys.stderr)
        print(f"Raw response:\n{raw_response}", file=sys.stderr)
        sys.exit(1)

    accepted, rejected = validate_mapping(raw_mapping, canonical_skills)

    merged, added, conflicts = merge_aliases(existing_aliases, accepted)

    sorted_merged = dict(sorted(merged.items()))
    with open(ALIASES_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_merged, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Existing aliases before: {len(existing_aliases)}")
    print(f"Aliases added: {len(added)}")
    print(f"Rejected by validation: {len(rejected)}")
    if rejected:
        print("Rejection reasons:")
        for reason in rejected:
            print(f"  - {reason}")
    if conflicts:
        print(f"Conflicts (existing entry kept): {len(conflicts)}")
        for conflict in conflicts:
            print(f"  - {conflict}")
    print()
    print(f"Total aliases after merge: {len(sorted_merged)}")
    print()
    print("Added entries:")
    for entry in added:
        print(f"  {entry}")


if __name__ == "__main__":
    main()
