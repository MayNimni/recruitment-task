"""CLI entry point.

    python main.py ingest                  # Flow A. reads data/, writes pool/
    python main.py match --job JOB001      # Flow B. reads pool/ + data/job_openings.csv, writes output/
    python main.py match --job JOB001 --llm  # Flow B + B7 model seam (ai_summary/ai_probes on the shortlist)
    python main.py run   --job JOB001      # both, in order
    python main.py index                   # rebuild index.html, the landing page

    python main.py ingest --data-dir data/edge_cases           # reads data/edge_cases/, writes pool/edge_cases/
    python main.py match --job JOB001 --data-dir data/edge_cases  # writes output/edge_cases/

--data-dir points ingest/match/run at any directory holding the same seven
files (four CSVs + three JSON configs) as data/ — a fixture set, most likely.
pool/ and output/ follow it: with the default data/, paths are pool/ and
output/ exactly as before; with any other --data-dir, they become
pool/<data-dir's name>/ and output/<data-dir's name>/, so a fixture run can
never collide with or overwrite the real one.

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
INDEX_TEMPLATE_PATH = REPO_ROOT / "index_template.html"

POOL_LIST_COLUMNS = [
    "past_companies", "past_titles", "skills_raw", "skills_canonical",
    "skills_alias_hits", "note_tags",
]
POOL_BOOL_COLUMNS = ["unverified", "flagged_on_site"]


DATA_DIR_HELP = (
    "directory holding the four source CSVs + three config JSONs (default: data/); "
    "pool/ and output/ paths follow it — see module docstring"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Flow A: read data/, write pool/")
    ingest_parser.add_argument("--data-dir", default=None, help=DATA_DIR_HELP)

    match_parser = subparsers.add_parser("match", help="Flow B: score a job against the pool")
    match_parser.add_argument("--job", required=True, help="job_id from data/job_openings.csv")
    match_parser.add_argument("--data-dir", default=None, help=DATA_DIR_HELP)
    match_parser.add_argument(
        "--llm", action="store_true",
        help="call the B7 model seam for the top shortlist_size candidates' ai_summary/ai_probes "
             "(requires ANTHROPIC_API_KEY); without it those two columns stay empty",
    )

    subparsers.add_parser(
        "index",
        help="rebuild index.html, the landing page linking every job's recruiter view",
    )

    run_parser = subparsers.add_parser("run", help="ingest then match")
    run_parser.add_argument("--job", required=True, help="job_id from data/job_openings.csv")
    run_parser.add_argument("--data-dir", default=None, help=DATA_DIR_HELP)
    run_parser.add_argument("--llm", action="store_true", help="see `match --llm`")

    return parser


def resolve_dirs(data_dir_arg: str | None):
    """--data-dir default (or explicit data/) resolves to the real DATA_DIR/
    POOL_DIR/OUTPUT_DIR unchanged. Any other --data-dir gets pool/output paths
    namespaced under its own name (pool/<name>/, output/<name>/), so a fixture
    run can never share a path with — and so can never overwrite — the real run.
    """
    data_dir = Path(data_dir_arg).resolve() if data_dir_arg else DATA_DIR
    if data_dir == DATA_DIR:
        return data_dir, POOL_DIR, OUTPUT_DIR
    return data_dir, REPO_ROOT / "pool" / data_dir.name, REPO_ROOT / "output" / data_dir.name


def run_ingest(data_dir: Path, pool_dir: Path) -> None:
    from ingest import load_sources
    from enrich import build_pool, write_pool

    sources = load_sources(data_dir)
    pool_df, edges_df = build_pool(sources)
    pool_path, edges_path = write_pool(pool_df, edges_df, pool_dir)

    print(f"wrote {len(pool_df)} pool rows to {pool_path}")
    print(f"wrote {len(edges_df)} referral edges to {edges_path}")


def run_index() -> None:
    """Rebuilds index.html from whatever reports currently exist under output/.

    Always describes the real data/ — a fixture run must not rewrite the
    landing page — so it takes no --data-dir and reads POOL_DIR/OUTPUT_DIR
    directly. Safe to run at any time; a job with no report yet simply renders
    as a card naming the command that produces one.
    """
    from ingest import load_jobs
    import output

    pool_rows, _ = read_pool(POOL_DIR)
    conference_count = len({r["conference_name"] for r in pool_rows if r.get("conference_name")})
    index_path = output.write_index(
        load_jobs(DATA_DIR), len(pool_rows), conference_count,
        INDEX_TEMPLATE_PATH, OUTPUT_DIR, REPO_ROOT,
        fixture_output_dir=OUTPUT_DIR / "edge_cases",
    )
    print(f"wrote landing page to {index_path}")


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
        record["overlap_years"] = int(record["overlap_years"]) if record.get("overlap_years") else 0
        edge_rows.append(record)

    return pool_rows, edge_rows


def find_job(jobs_df, job_id: str) -> dict:
    matches = jobs_df[jobs_df["job_id"] == job_id]
    if matches.empty:
        valid_ids = ", ".join(sorted(jobs_df["job_id"]))
        sys.exit(f"unknown job_id '{job_id}'. Valid ids: {valid_ids}")
    return matches.iloc[0].to_dict()


def run_match(job_id: str, use_llm: bool, data_dir: Path, pool_dir: Path, output_dir: Path) -> None:
    if not any(pool_dir.glob("*.csv")):
        sys.exit(
            f"{pool_dir} is empty or missing — run "
            f"`python main.py ingest --data-dir {data_dir}` first (looked in {pool_dir})"
        )

    from ingest import load_company_domains, load_jobs, load_skill_aliases, load_title_families
    import output
    import score

    jobs_df = load_jobs(data_dir)
    job_row = find_job(jobs_df, job_id)
    aliases = load_skill_aliases(data_dir)
    title_families = load_title_families(data_dir)
    company_domains = load_company_domains(data_dir)

    pool_rows, edge_rows = read_pool(pool_dir)
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

    # The shortlist summary line is model-or-nothing by design: there is no
    # deterministic template behind it, so a run without --llm — or a failed
    # model call — leaves it "" and write_recruiter_view renders no line at all.
    summary_line = ""

    if use_llm:
        import llm

        client = llm.build_client()
        output.apply_llm_briefs(match_rows, pool_rows_by_id, requirements, client)
        filled = sum(1 for r in match_rows if r["ai_summary"])
        print(f"ai_summary written for {filled} of {len(match_rows)} rows")
        summary_line = output.apply_llm_job_summary(match_rows, pool_rows_by_id, requirements, client) or ""

    matches_path = output.write_matches_csv(match_rows, output_dir)

    data_items = output.build_data_items(match_rows, pool_rows_by_id)
    conference_count = len({r["conference_name"] for r in pool_rows if r.get("conference_name")})
    html_path = output.write_recruiter_view(
        data_items, job_row, len(pool_rows), conference_count,
        score.DEFAULT_WEIGHTS, TEMPLATE_PATH, output_dir, summary_line,
    )

    print(f"wrote {len(match_rows)} matches to {matches_path}")
    print(f"wrote recruiter view to {html_path}")

    # Keep the landing page in step with the reports it links, but only on a
    # real-data run: a --data-dir fixture must never rewrite index.html.
    if output_dir == OUTPUT_DIR:
        run_index()


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "index":
        run_index()
        return

    data_dir, pool_dir, output_dir = resolve_dirs(args.data_dir)

    if args.command == "ingest":
        run_ingest(data_dir, pool_dir)
    elif args.command == "match":
        run_match(args.job, args.llm, data_dir, pool_dir, output_dir)
    elif args.command == "run":
        run_ingest(data_dir, pool_dir)
        run_match(args.job, args.llm, data_dir, pool_dir, output_dir)


if __name__ == "__main__":
    main()
