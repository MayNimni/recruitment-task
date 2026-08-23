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
    }
