"""A1 — load the four source CSVs and three config JSON files from data/.

No job data is read here (job_openings.csv is loaded but never inspected by Flow A;
Flow B's B1 step is the one that reads it). No network calls, no parsing beyond
what pandas/json do for us.
"""

import json
from pathlib import Path

import pandas as pd

ATTENDEES_FILE = "conference_attendees.csv"
PROFILES_FILE = "linkedin_profiles.csv"
EMPLOYEES_FILE = "wsc_employees.csv"
JOBS_FILE = "job_openings.csv"

SKILL_ALIASES_FILE = "skill_aliases.json"
TITLE_FAMILIES_FILE = "title_families.json"
COMPANY_DOMAINS_FILE = "company_domains.json"
REFERRAL_FEEDBACK_FILE = "referral_feedback.csv"


def load_csv(data_dir: Path, filename: str) -> pd.DataFrame:
    """Read one CSV from data_dir, keeping every column as string.

    dtype=str avoids pandas guessing numeric types for id-like columns (HS001,
    WSC001) and preserves list-valued columns as plain semicolon-joined strings
    for enrich.py to split. Empty cells become '' rather than NaN so downstream
    string operations don't need null-checks everywhere.
    """
    path = data_dir / filename
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_json_config(data_dir: Path, filename: str) -> dict:
    path = data_dir / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_attendees(data_dir: Path) -> pd.DataFrame:
    return load_csv(data_dir, ATTENDEES_FILE)


def load_profiles(data_dir: Path) -> pd.DataFrame:
    df = load_csv(data_dir, PROFILES_FILE)
    df["years_experience"] = pd.to_numeric(df["years_experience"], errors="coerce")
    return df


def load_employees(data_dir: Path) -> pd.DataFrame:
    return load_csv(data_dir, EMPLOYEES_FILE)


def load_jobs(data_dir: Path) -> pd.DataFrame:
    return load_csv(data_dir, JOBS_FILE)


def load_skill_aliases(data_dir: Path) -> dict:
    return load_json_config(data_dir, SKILL_ALIASES_FILE)


def load_title_families(data_dir: Path) -> dict:
    return load_json_config(data_dir, TITLE_FAMILIES_FILE)


def load_company_domains(data_dir: Path) -> dict:
    return load_json_config(data_dir, COMPANY_DOMAINS_FILE)


def load_referral_feedback(data_dir: Path) -> dict:
    """A1, optional. Returns {(hubspot_id, employee_id): feedback} or {} if the
    file is absent.

    referral_feedback is not derived from any source record — in production it
    is written by the recruiter through HubSpot after they ask the colleague,
    and ingestion only carries whatever is already on record. This file is that
    record. data/ ships without one, so every real edge is 'not_requested';
    data/edge_cases/ ships one so the retired-edge branch (SPEC.md B5) is
    reproduced by `ingest` rather than hand-written into pool/.
    """
    path = data_dir / REFERRAL_FEEDBACK_FILE
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return {
        (row["hubspot_id"], row["employee_id"]): row["referral_feedback"]
        for _, row in df.iterrows()
        if row["referral_feedback"].strip()
    }


def load_sources(data_dir) -> dict:
    """A1. Returns the four source dataframes plus the three config dicts.

    Flow A uses attendees, profiles, employees, skill_aliases. jobs is loaded
    here (per SPEC.md §0) but Flow A must never read it: it is for B1's use.
    title_families and company_domains are likewise Flow B-only configs,
    loaded here for the same reason jobs is: A1 is the single ingestion point
    for everything under data/.
    """
    data_dir = Path(data_dir)
    return {
        "attendees": load_attendees(data_dir),
        "profiles": load_profiles(data_dir),
        "employees": load_employees(data_dir),
        "jobs": load_jobs(data_dir),
        "skill_aliases": load_skill_aliases(data_dir),
        "title_families": load_title_families(data_dir),
        "company_domains": load_company_domains(data_dir),
        "referral_feedback": load_referral_feedback(data_dir),
    }
