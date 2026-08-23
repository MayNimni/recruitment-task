"""CLI entry point.

    python main.py ingest                  # Flow A. reads data/, writes pool/
    python main.py match --job JOB001      # Flow B. reads pool/ + data/job_openings.csv, writes output/
    python main.py run   --job JOB001      # both, in order

Owns argument parsing and sequencing only — no scoring or enrichment logic
lives here (ARCHITECTURE.md §7). ingest/enrich and score/output never import
each other, so main.py is also where B2's read_pool lives and where a
scored candidate's score.py values are joined with its output.py referral
edge into one row.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
POOL_DIR = REPO_ROOT / "pool"
OUTPUT_DIR = REPO_ROOT / "output"
TEMPLATE_PATH = REPO_ROOT / "recruiter_view.html"

POOL_LIST_COLUMNS = [
    "past_companies", "past_titles", "skills_raw", "skills_canonical",
    "skills_alias_hits", "note_tags",
]
POOL_BOOL_COLUMNS = ["unverified", "flagged_on_site"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ingest", help="Flow A: read data/, write pool/")

    match_parser = subparsers.add_parser("match", help="Flow B: score a job against the pool")
    match_parser.add_argument("--job", required=True, help="job_id from data/job_openings.csv")

    run_parser = subparsers.add_parser("run", help="ingest then match")
    run_parser.add_argument("--job", required=True, help="job_id from data/job_openings.csv")

    return parser


def run_ingest() -> None:
    from ingest import load_sources
    from enrich import build_pool, write_pool

    sources = load_sources(DATA_DIR)
    pool_df, edges_df = build_pool(sources)
    pool_path, edges_path = write_pool(pool_df, edges_df, POOL_DIR)

    print(f"wrote {len(pool_df)} pool rows to {pool_path}")
    print(f"wrote {len(edges_df)} referral edges to {edges_path}")


def read_pool(pool_dir):
    """B2. Reads and deserializes the two pool CSVs into plain dicts:
    ';'-joined list columns become Python lists, 'True'/'False' strings
    become bools, years_experience becomes float (NaN if blank/unverified).
    """
    pool_dir = Path(pool_dir)
    pool_df = pd.read_csv(pool_dir / "talent_pool.csv", dtype=str, keep_default_na=False)
    edges_df = pd.read_csv(pool_dir / "referral_edges.csv", dtype=str, keep_default_na=False)

    pool_rows = []
    for record in pool_df.to_dict(orient="records"):
        for col in POOL_LIST_COLUMNS:
            record[col] = [item.strip() for item in record[col].split(";") if item.strip()]
        for col in POOL_BOOL_COLUMNS:
            record[col] = record[col] == "True"
        years = record.get("years_experience", "")
        record["years_experience"] = float(years) if years else float("nan")
        pool_rows.append(record)

    edge_rows = []
    for record in edges_df.to_dict(orient="records"):
        record["mutual_count"] = int(record["mutual_count"]) if record.get("mutual_count") else 0
        edge_rows.append(record)

    return pool_rows, edge_rows


def find_job(jobs_df, job_id: str) -> dict:
    matches = jobs_df[jobs_df["job_id"] == job_id]
    if matches.empty:
        valid_ids = ", ".join(sorted(jobs_df["job_id"]))
        sys.exit(f"unknown job_id '{job_id}'. Valid ids: {valid_ids}")
    return matches.iloc[0].to_dict()


def run_match(job_id: str) -> None:
    if not any(POOL_DIR.glob("*.csv")):
        sys.exit(
            f"pool/ is empty or missing — run `python main.py ingest` first "
            f"(looked in {POOL_DIR})"
        )

    from ingest import load_company_domains, load_jobs, load_skill_aliases, load_title_families
    import output
    import score

    jobs_df = load_jobs(DATA_DIR)
    job_row = find_job(jobs_df, job_id)
    aliases = load_skill_aliases(DATA_DIR)
    title_families = load_title_families(DATA_DIR)
    company_domains = load_company_domains(DATA_DIR)

    pool_rows, edge_rows = read_pool(POOL_DIR)
    requirements = score.parse_job(job_row, aliases, title_families, company_domains)

    edges_by_candidate = {}
    for edge in edge_rows:
        edges_by_candidate.setdefault(edge["hubspot_id"], []).append(edge)

    scored_rows = []
    pool_rows_by_id = {}
    for pool_row in pool_rows:
        pool_rows_by_id[pool_row["hubspot_id"]] = pool_row
        pool_row["_skills_lists"] = score.skills_lists(pool_row, requirements)
        values, basis = score.score_components(pool_row, requirements)
        scored_rows.append({
            "hubspot_id": pool_row["hubspot_id"],
            "years_experience": pool_row["years_experience"],
            "values": values,
            "score_basis": basis,
        })

    ranked = score.rank(scored_rows, score.DEFAULT_WEIGHTS)

    match_rows = [
        output.build_match_row(
            job_row,
            pool_rows_by_id[scored["hubspot_id"]],
            requirements,
            scored,
            edges_by_candidate.get(scored["hubspot_id"], []),
        )
        for scored in ranked
    ]

    matches_path = output.write_matches_csv(match_rows, OUTPUT_DIR)

    data_items = output.build_data_items(match_rows, pool_rows_by_id)
    conference_count = len({r["conference_name"] for r in pool_rows if r.get("conference_name")})
    html_path = output.write_recruiter_view(
        data_items, job_row, len(pool_rows), conference_count,
        score.DEFAULT_WEIGHTS, TEMPLATE_PATH, OUTPUT_DIR,
    )

    print(f"wrote {len(match_rows)} matches to {matches_path}")
    print(f"wrote recruiter view to {html_path}")


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        run_ingest()
    elif args.command == "match":
        run_match(args.job)
    elif args.command == "run":
        run_ingest()
        run_match(args.job)


if __name__ == "__main__":
    main()
