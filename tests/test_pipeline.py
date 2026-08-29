"""The acceptance checks behind SPEC.md §11 ("Done means") and the §0 invariants.

    python3 -m unittest discover tests

Standard library plus pandas — the same dependency list as the pipeline, so a
reviewer who can run the pipeline can run the tests. No network, no API key.

Two of these tests re-run the real pipeline and compare the result against the
artifacts committed in the repo. That is deliberate: the claim under test is
"a clean checkout reproduces every committed artifact exactly", and the only
honest way to test it is to reproduce them. Both flows are deterministic, so a
passing run rewrites the files byte-for-byte and leaves the working tree clean;
a failing run is exactly the signal that a committed artifact has gone stale.
"""

import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

import enrich  # noqa: E402
import ingest  # noqa: E402
import main as pipeline_main  # noqa: E402
import output  # noqa: E402
import score  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "pipeline" / "main.py"), *args],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def read_matches(job_id="JOB001", subdir=None):
    directory = REPO_ROOT / "output" / subdir if subdir else REPO_ROOT / "output"
    with open(directory / f"{job_id}_matches.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------
# SPEC §11 — "run --job JOB001 produces four files, and two runs agree"
# --------------------------------------------------------------------------

class TestReproducibility(unittest.TestCase):
    ARTIFACTS = [
        Path("pool/talent_pool.csv"),
        Path("pool/referral_edges.csv"),
        Path("output/JOB001_matches.csv"),
        Path("output/JOB001_recruiter_view.html"),
    ]

    def test_run_reproduces_the_committed_artifacts_byte_for_byte(self):
        before = {p: sha256(REPO_ROOT / p) for p in self.ARTIFACTS}

        result = run_cli("run", "--job", "JOB001")
        self.assertEqual(result.returncode, 0, result.stderr)

        for path, digest in before.items():
            with self.subTest(artifact=str(path)):
                self.assertEqual(sha256(REPO_ROOT / path), digest,
                                 f"{path} differs from the committed version")

    def test_two_consecutive_runs_are_identical(self):
        run_cli("run", "--job", "JOB001")
        first = {p: sha256(REPO_ROOT / p) for p in self.ARTIFACTS}
        run_cli("run", "--job", "JOB001")
        for path, digest in first.items():
            with self.subTest(artifact=str(path)):
                self.assertEqual(sha256(REPO_ROOT / path), digest)

    def test_default_path_loads_no_network_library(self):
        """SPEC §11: no module reachable from a default run imports a network library."""
        probe = (
            "import sys; sys.path.insert(0, %r);"
            "import main, ingest, enrich, score, output;"
            "print([m for m in sys.modules if m.split('.')[0] in "
            "{'anthropic', 'requests', 'httpx', 'urllib3', 'socket', 'llm'}])"
            % str(REPO_ROOT / "pipeline")
        )
        result = subprocess.run([sys.executable, "-c", probe],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]", "a default import pulled in a network library")


# --------------------------------------------------------------------------
# SPEC §11 — "every number traces to value x weight"
# --------------------------------------------------------------------------

class TestScoreArithmetic(unittest.TestCase):
    def test_round_half_up_not_half_to_even(self):
        """A raw 86.5 must round the same way here as Math.round does in the view."""
        self.assertEqual(score.round_half_up(86.5), 87)
        self.assertEqual(score.round_half_up(0.5), 1)
        self.assertEqual(score.round_half_up(2.5), 3)   # round() would give 2
        self.assertEqual(round(2.5), 2)                 # the behaviour being avoided

    def test_weights_sum_to_100(self):
        self.assertEqual(sum(score.DEFAULT_WEIGHTS.values()), 100)

    def test_every_match_score_traces_to_value_times_weight(self):
        weights = score.DEFAULT_WEIGHTS
        for row in read_matches():
            basis = [c.strip() for c in row["score_basis"].split(";") if c.strip()]
            weight_sum = sum(weights[name] for name in basis)
            weighted = sum(float(row[f"value_{name}"]) * weights[name] for name in basis)
            with self.subTest(candidate=row["full_name"]):
                self.assertEqual(int(row["match_score"]),
                                 score.round_half_up(weighted / weight_sum * 100))

    def test_points_columns_equal_value_times_weight(self):
        for row in read_matches():
            for name, weight in score.DEFAULT_WEIGHTS.items():
                if row[f"value_{name}"] == "":
                    continue
                with self.subTest(candidate=row["full_name"], component=name):
                    self.assertAlmostEqual(float(row[f"points_{name}"]),
                                           float(row[f"value_{name}"]) * weight, places=3)

    def test_every_component_value_is_within_zero_and_one(self):
        for row in read_matches():
            for name in score.ALL_COMPONENTS:
                raw = row[f"value_{name}"]
                if raw == "":
                    continue
                with self.subTest(candidate=row["full_name"], component=name):
                    self.assertGreaterEqual(float(raw), 0.0)
                    self.assertLessEqual(float(raw), 1.0)


# --------------------------------------------------------------------------
# Missing data must not read as poor fit (DESIGN.md §2, assumption 2)
# --------------------------------------------------------------------------

class TestUnverified(unittest.TestCase):
    JOB = {"job_id": "JOB001", "title": "Senior ML Engineer", "department": "AI/ML",
           "seniority": "Senior", "key_domains": "computer vision",
           "required_skills": "Python;PyTorch", "nice_to_have": ""}

    def requirements(self):
        return score.parse_job(self.JOB, {}, {"AI/ML": ["engineer"]}, {})

    def test_unverified_row_scores_only_the_three_computable_components(self):
        row = {"unverified": True, "current_title": "ML Engineer", "note_tags": [],
               "skills_canonical": [], "years_experience": float("nan"),
               "industry": "", "past_companies": [], "conference_domain": "",
               "note_raw": ""}
        values, basis = score.score_components(row, self.requirements())
        self.assertEqual(basis, ["title", "notes", "conference"])
        for name in ["skills", "experience", "industry", "past"]:
            with self.subTest(component=name):
                self.assertIsNone(values[name], "an uncomputable component must be None, never 0")

    def test_unverified_total_is_normalized_over_37_not_100(self):
        basis = score.UNVERIFIED_COMPONENTS
        self.assertEqual(sum(score.DEFAULT_WEIGHTS[name] for name in basis), 37)

    def test_verified_row_scores_all_seven(self):
        row = {"unverified": False, "current_title": "ML Engineer", "note_tags": [],
               "skills_canonical": ["Python"], "years_experience": 8.0,
               "industry": "Sports", "past_companies": [], "conference_domain": "",
               "note_raw": ""}
        values, basis = score.score_components(row, self.requirements())
        self.assertEqual(basis, score.ALL_COMPONENTS)
        self.assertTrue(all(v is not None for v in values.values()))

    def test_the_fixture_unverified_row_is_labelled_in_the_committed_output(self):
        rows = read_matches(subdir="edge_cases")
        unverified = [r for r in rows if r["unverified"] == "True"]
        self.assertTrue(unverified, "the fixture should contain an unverified candidate")
        for row in unverified:
            self.assertEqual(row["score_basis"], "title;notes;conference")
            self.assertEqual(row["value_skills"], "", "an unscored component must be blank, not 0")


# --------------------------------------------------------------------------
# Nobody is filtered out (DESIGN.md §1, "Why nothing is filtered")
# --------------------------------------------------------------------------

class TestNobodyIsDropped(unittest.TestCase):
    def test_every_pool_row_appears_in_the_output(self):
        pool_rows, _ = pipeline_main.read_pool(REPO_ROOT / "pool")
        matches = read_matches()
        self.assertEqual(len(matches), len(pool_rows))
        self.assertEqual({r["hubspot_id"] for r in matches},
                         {r["hubspot_id"] for r in pool_rows})

    def test_a_candidate_with_no_referral_edge_is_still_ranked(self):
        pool_rows, edge_rows = pipeline_main.read_pool(REPO_ROOT / "pool")
        with_edges = {e["hubspot_id"] for e in edge_rows}
        without = [r["hubspot_id"] for r in pool_rows if r["hubspot_id"] not in with_edges]
        self.assertTrue(without, "the supplied data should contain candidates with no edge")
        scored = {r["hubspot_id"]: r for r in read_matches()}
        for hubspot_id in without:
            with self.subTest(candidate=hubspot_id):
                self.assertIn(hubspot_id, scored)
                self.assertEqual(scored[hubspot_id]["referral_name"], "")

    def test_an_off_domain_attendee_is_ranked_low_rather_than_removed(self):
        """The IT manager from a hospital is scored, and sinks. DESIGN.md §1."""
        rows = read_matches()
        by_id = {r["full_name"]: i for i, r in enumerate(rows)}
        self.assertIn("Laura Gibson", by_id)
        self.assertGreater(by_id["Laura Gibson"], len(rows) // 2)


# --------------------------------------------------------------------------
# Flow A — edges, tiers, and the matching that must not over-reach
# --------------------------------------------------------------------------

class TestCompanyMatching(unittest.TestCase):
    def test_intel_does_not_match_an_idf_intelligence_unit(self):
        """Substring matching would pair them. Whole-token matching must not."""
        self.assertFalse(enrich.company_tokens("Intel (2015-2018)")
                         & enrich.company_tokens("IDF Intelligence Unit (2014-2017)"))

    def test_generic_tokens_are_dropped_after_stemming(self):
        """'sports' on the drop-list and 'sport' in a name must be the same token."""
        self.assertNotIn("sport", enrich.company_tokens("Global Sports Group"))
        self.assertIn("opta", enrich.company_tokens("Opta Sports (2019-2021)"))

    def test_short_tokens_never_carry_a_match(self):
        self.assertFalse(any(len(t) < 4 for t in enrich.company_tokens("NBA IBM Ltd")))

    def test_an_unparseable_tenure_is_treated_as_no_overlap_never_guessed(self):
        self.assertEqual(enrich.compute_overlap(None, (2015, 2018)), (0, ""))
        self.assertEqual(enrich.compute_overlap((2019, 2021), (2015, 2018)), (0, ""))


class TestTiers(unittest.TestCase):
    def edge(self, shared="", overlap=0, mutual=0):
        return {"shared_employer": shared, "overlap_years": overlap, "mutual_count": mutual}

    def test_the_tier_table(self):
        cases = [
            (self.edge("Opta", 2, 1), "A"),   # together, and a mutual connection
            (self.edge("", 0, 3), "B"),       # no shared employer, 3 mutuals
            (self.edge("", 0, 2), "C"),
            (self.edge("Opta", 0, 1), "C"),   # shared, no overlap, but a mutual
            (self.edge("Opta", 2, 0), "C"),   # shared and overlapping, no mutual
            (self.edge("Opta", 0, 0), "D"),   # weakest signal that still earns a row
        ]
        for edge, expected in cases:
            with self.subTest(edge=edge):
                self.assertEqual(enrich.assign_tier(edge), expected)

    def test_an_edge_matching_no_tier_raises_rather_than_returning_empty(self):
        with self.assertRaises(AssertionError):
            enrich.assign_tier(self.edge("", 0, 0))

    def test_every_committed_edge_carries_a_tier(self):
        _, edge_rows = pipeline_main.read_pool(REPO_ROOT / "pool")
        self.assertTrue(edge_rows)
        for edge in edge_rows:
            self.assertIn(edge["tier"], {"A", "B", "C", "D"})


# --------------------------------------------------------------------------
# A8 — conference-domain relevance (the signal-to-noise question)
# --------------------------------------------------------------------------

DISCIPLINE = {"kind": "discipline",
              "title_families": ["devops engineer", "platform engineer"],
              "skills": ["kubernetes", "terraform", "ci/cd"]}
SUBJECT = {"kind": "subject",
           "skills": ["sports", "sports analytics", "athlete"],
           "industry_core": ["sport"],
           "industry_adjacent": ["video", "broadcast", "media"]}
DOMAINS = {"DevOps": DISCIPLINE, "Sports Technology": SUBJECT}


def relevance(domain="DevOps", **row):
    row.setdefault("conference_domain", domain)
    return enrich.score_conference_relevance(row, DOMAINS)


class TestConferenceRelevance(unittest.TestCase):
    def test_a_discipline_event_ignores_the_employer_sector(self):
        """A DevOps engineer at an agriculture company is a DevOps person."""
        result = relevance(current_title="DevOps Engineer",
                           skills_canonical=["Kubernetes", "Terraform"],
                           industry="Agriculture Technology")
        self.assertEqual(result["conference_class"], "core")
        self.assertEqual(result["conference_relevance"], 100)

    def test_a_subject_event_reads_industry_and_skills_not_the_title(self):
        """Every discipline attends a sports-tech summit, so the title says nothing."""
        insider = relevance("Sports Technology", current_title="Backend Engineer",
                            skills_canonical=["Python", "Sports Analytics"],
                            industry="Sports Technology")
        outsider = relevance("Sports Technology", current_title="Backend Engineer",
                             skills_canonical=["Python", "Java"], industry="Banking")
        self.assertEqual(insider["conference_class"], "core")
        self.assertEqual(outsider["conference_class"], "off_domain")
        self.assertEqual(outsider["conference_relevance"], 0)

    def test_an_adjacent_industry_scores_between_the_two(self):
        result = relevance("Sports Technology", current_title="Backend Engineer",
                           skills_canonical=["Python"], industry="Broadcast Media")
        self.assertEqual(result["conference_class"], "adjacent")
        self.assertIn("adjacent to this event", result["conference_relevance_why"])

    def test_an_unknown_conference_domain_is_unassessed_never_a_zero(self):
        result = relevance("Underwater Basket Weaving", current_title="DevOps Engineer",
                           skills_canonical=["Kubernetes"], industry="Sports")
        self.assertEqual(result["conference_class"], "unassessed")
        self.assertEqual(result["conference_relevance"], "")

    def test_a_title_only_read_can_confirm_but_cannot_condemn(self):
        """Evidence of presence is conclusive where evidence of absence is not:
        an unverified row has no skills, so a title that misses cannot mean noise."""
        hit = relevance(current_title="Platform Engineer", skills_canonical=[], industry="")
        miss = relevance(current_title="Marketing Associate", skills_canonical=[], industry="")
        self.assertEqual(hit["conference_class"], "core")
        self.assertEqual(miss["conference_class"], "unassessed")
        self.assertEqual(miss["conference_relevance"], 0)
        self.assertIn("missing", miss["conference_relevance_why"])

    def test_nothing_readable_at_all_is_unassessed(self):
        result = relevance("Sports Technology", current_title="Anything",
                           skills_canonical=[], industry="")
        self.assertEqual(result["conference_class"], "unassessed")
        self.assertEqual(result["conference_relevance"], "")

    def test_vocabulary_matching_is_whole_word_and_plural_insensitive(self):
        self.assertEqual(enrich.domain_hits("Sports Analytics", ["sport"]), ["sport"])
        self.assertEqual(enrich.domain_hits("Sport Science", ["sports"]), ["sports"])
        self.assertEqual(enrich.domain_hits("Videographer", ["video"]), [])
        # a multi-word token has to appear in order and unbroken
        self.assertEqual(enrich.domain_hits("Sports domain knowledge", ["sports domain"]),
                         ["sports domain"])
        self.assertEqual(enrich.domain_hits("Domain knowledge of sports", ["sports domain"]), [])

    def test_a_qualifier_after_the_dash_still_counts_as_domain_vocabulary(self):
        """B3 drops ' - Video' to read the head noun. For a broadcast event that
        qualifier is the signal, so the vocabulary check keeps the whole title."""
        self.assertEqual(enrich.title_head("Senior ML Engineer - Video"), "ml engineer")
        broadcast = {"kind": "discipline", "title_families": ["video engineer"],
                     "skills": ["video", "ffmpeg"]}
        result = enrich.score_conference_relevance(
            {"conference_domain": "B", "current_title": "Senior ML Engineer - Video",
             "skills_canonical": ["Python"], "industry": ""}, {"B": broadcast})
        self.assertGreater(result["conference_relevance"], 0)
        self.assertIn("title mentions video", result["conference_relevance_why"])


class TestRelevanceStaysOutOfTheScore(unittest.TestCase):
    """A8 answers "who was in the room", B3 answers "who fits this role".
    They must never touch: relevance carries no weight and no column in the
    match output (DESIGN.md §1)."""

    def test_no_relevance_component_exists_in_the_scoring_weights(self):
        self.assertNotIn("relevance", score.DEFAULT_WEIGHTS)
        for name in score.ALL_COMPONENTS:
            self.assertNotIn("conference_class", name)

    def test_the_matches_csv_carries_no_relevance_column(self):
        for column in read_matches()[0]:
            self.assertNotIn("conference_relevance", column)
            self.assertNotIn("conference_class", column)

    def test_scoring_a_row_ignores_its_relevance_fields(self):
        requirements = TestUnverified().requirements()
        base = {"unverified": False, "current_title": "ML Engineer", "note_tags": [],
                "skills_canonical": ["Python"], "years_experience": 8.0,
                "industry": "Sports", "past_companies": [], "conference_domain": "",
                "note_raw": ""}
        plain, _ = score.score_components(dict(base), requirements)
        labelled, _ = score.score_components(
            dict(base, conference_relevance=0, conference_class="off_domain"), requirements)
        self.assertEqual(plain, labelled)


class TestRelevanceInTheCommittedPool(unittest.TestCase):
    def pool(self, subdir=None):
        directory = REPO_ROOT / "pool" / subdir if subdir else REPO_ROOT / "pool"
        with open(directory / "talent_pool.csv", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_every_row_carries_one_of_the_four_classes(self):
        for row in self.pool():
            self.assertIn(row["conference_class"],
                          {"core", "adjacent", "off_domain", "unassessed"})

    def test_the_class_agrees_with_the_score_on_every_row(self):
        for row in self.pool():
            score_text, classification = row["conference_relevance"], row["conference_class"]
            with self.subTest(candidate=row["full_name"]):
                if classification == "unassessed":
                    continue          # blank, or a zero on an incomplete read
                self.assertNotEqual(score_text, "")
                value = int(score_text)
                if classification == "core":
                    self.assertGreaterEqual(value, enrich.CONFERENCE_CORE_THRESHOLD)
                elif classification == "adjacent":
                    self.assertTrue(0 < value < enrich.CONFERENCE_CORE_THRESHOLD)
                else:
                    self.assertEqual(value, 0)

    def test_every_row_carries_a_reason(self):
        for row in self.pool():
            self.assertTrue(row["conference_relevance_why"].strip(), row["full_name"])

    def test_the_off_domain_attendees_are_the_ones_the_brief_names(self):
        """The hospital IT manager and the telecom network engineer are scored,
        classified off-domain for their event, and still in the pool."""
        by_name = {r["full_name"]: r for r in self.pool()}
        self.assertEqual(by_name["Laura Gibson"]["conference_class"], "off_domain")
        self.assertEqual(by_name["Patrick Duval"]["conference_class"], "off_domain")
        self.assertEqual(len(self.pool()), 75, "classification must never drop a row")

    def test_a_practitioner_outside_our_industry_is_still_core_to_their_event(self):
        """An analytics engineer at an insurer belongs at a data summit. Relevance
        to the event is not the same question as fit for a sports-tech role."""
        by_name = {r["full_name"]: r for r in self.pool()}
        self.assertEqual(by_name["Katarzyna Wojcik"]["current_title"], "Analytics Engineer")
        self.assertEqual(by_name["Katarzyna Wojcik"]["industry"], "Insurance")
        self.assertEqual(by_name["Katarzyna Wojcik"]["conference_class"], "core")

    def test_the_fixture_exercises_all_four_classes(self):
        classes = {r["conference_class"] for r in self.pool("edge_cases")}
        self.assertEqual(classes, {"core", "adjacent", "off_domain", "unassessed"})

    def test_the_landing_page_cannot_disagree_with_the_pool(self):
        html = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
        match = re.search(r"const CONFERENCES = (\[.*?\]);", html, re.S)
        self.assertIsNotNone(match, "index.html should carry the CONFERENCES literal")
        page = {entry["name"]: entry for entry in json.loads(match.group(1))}

        counts = {}
        for row in self.pool():
            event = counts.setdefault(row["conference_name"],
                                      {"attendees": 0, "core": 0, "adjacent": 0,
                                       "off_domain": 0, "unassessed": 0})
            event["attendees"] += 1
            event[row["conference_class"]] += 1

        self.assertEqual(set(page), set(counts))
        for name, expected in counts.items():
            for key, value in expected.items():
                with self.subTest(event=name, field=key):
                    self.assertEqual(page[name][key], value)


# --------------------------------------------------------------------------
# Flow B — referral selection never overstates
# --------------------------------------------------------------------------

class TestReferralSelection(unittest.TestCase):
    def edge(self, tier, dept="AI/ML", mutual=1, feedback="not_requested"):
        return {"tier": tier, "employee_department": dept, "mutual_count": mutual,
                "referral_feedback": feedback, "shared_employer": "", "overlap_years": 0}

    def test_an_insufficient_edge_is_retired_before_selection(self):
        best = self.edge("A", feedback="insufficient")
        fallback = self.edge("C")
        chosen = output.select_referral([best, fallback], "AI/ML")
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["tier"], "C")

    def test_higher_tier_wins_then_department_distance(self):
        far = self.edge("A", dept="Finance")
        near = self.edge("B", dept="AI/ML")
        self.assertEqual(output.select_referral([near, far], "AI/ML")[0]["tier"], "A")
        same_tier = [self.edge("B", dept="Finance"), self.edge("B", dept="AI/ML")]
        self.assertEqual(output.select_referral(same_tier, "AI/ML")[0]["employee_department"],
                         "AI/ML")

    def test_worked_together_is_written_only_when_the_dates_back_it_up(self):
        overlapping = {"shared_employer": "Mobileye", "overlap_years": 2,
                       "overlap_period": "2019-2021", "mutual_count": 1}
        self.assertEqual(output.referral_why(overlapping),
                         "worked together at Mobileye, 2019-2021")

        no_overlap = {"shared_employer": "Mobileye", "overlap_years": 0,
                      "overlap_period": "", "mutual_count": 1}
        self.assertEqual(output.referral_why(no_overlap),
                         "both worked at Mobileye, no overlapping years")
        self.assertNotIn("worked together", output.referral_why(no_overlap))

    def test_mutual_connection_count_is_singular_at_one(self):
        self.assertEqual(output.referral_why({"shared_employer": "", "mutual_count": 1}),
                         "1 mutual connection")
        self.assertEqual(output.referral_why({"shared_employer": "", "mutual_count": 3}),
                         "3 mutual connections")

    def test_no_committed_row_claims_a_shared_employer_without_one(self):
        for row in read_matches():
            if row["referral_why"].startswith("worked together"):
                with self.subTest(candidate=row["full_name"]):
                    self.assertRegex(row["referral_why"], r"\d{4}-\d{4}$")


# --------------------------------------------------------------------------
# CLI failure modes (SPEC §2)
# --------------------------------------------------------------------------

class TestFailureModes(unittest.TestCase):
    def test_unknown_job_id_exits_non_zero_and_lists_the_valid_ids(self):
        result = run_cli("match", "--job", "JOB999")
        self.assertNotEqual(result.returncode, 0)
        message = result.stdout + result.stderr
        self.assertIn("JOB001", message)

    def test_an_empty_pool_exits_non_zero_naming_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                pipeline_main.run_match("JOB001", False, REPO_ROOT / "data",
                                        Path(tmp), Path(tmp))
        self.assertIn("ingest", str(caught.exception))

    def test_an_unknown_seniority_raises_and_names_the_job(self):
        with self.assertRaises(ValueError) as caught:
            score.parse_job({"job_id": "JOB404", "title": "x", "department": "AI/ML",
                             "seniority": "Overlord"}, {}, {}, {})
        self.assertIn("JOB404", str(caught.exception))

    def test_a_candidate_with_no_linkedin_match_is_kept_not_dropped(self):
        sources = ingest.load_sources(REPO_ROOT / "data" / "edge_cases")
        pool_df, _ = enrich.build_pool(sources)
        self.assertTrue(pool_df["unverified"].any(),
                        "the fixture must exercise the unmatched-profile branch")
        self.assertEqual(len(pool_df), len(sources["attendees"]),
                         "an unmatched attendee must still produce a pool row")


if __name__ == "__main__":
    unittest.main()
