"""B7 model seam, live implementation: one model call per shortlisted candidate,
producing the free-text ai_summary/ai_probes pair that sits beside (never over)
output.py's deterministic rationale/interview_probes template.

Only imported when `python main.py match --llm` is used — main.py and output.py
run with no API key otherwise, and build_rationale/build_interview_probes stay the
default (ARCHITECTURE.md §8). Client construction mirrors build_aliases.py's
call_model: same ANTHROPIC_API_KEY / anthropic-package checks, same model id.
"""

import json
import os
import sys

MODEL = "claude-sonnet-4-6"

PROMPT_TEMPLATE = """Write the two lines a recruiter reads beside one candidate on a ranked shortlist:
why this person is worth a call, and what to ask them.

THE ROLE
__JOB_SUMMARY__

THE CANDIDATE — the complete record. Nothing outside it is known.
__CANDIDATE__

"ai_summary" — one or two sentences, 40 words max. Connect their skills to where
they worked and say what that means for THIS role. The match score, the component
values and the skill lists are already in their own columns; restating them wastes
the only two sentences you get. Write what the columns cannot say.

"ai_probes" — one or two questions for the first call, 35 words max. Each must
arise from a specific gap in the record and be answerable in conversation.
"Tell us about yourself" is a failure.

RULES
1. Use only facts in the record. To say someone worked in automotive vision, the
   employer must be in past_companies. Never infer an employer, a seniority, a
   technology or a motivation that is not written above.
2. unverified: true means no enriched profile — only what they typed at
   registration. Say the record is thin rather than writing confidently from nothing.
3. No candidate name. No marketing language, no bullet points, no "appears to be".
4. Name a missing required skill once, in whichever field it belongs.

EXAMPLE — shape, length and register only. Do not reuse its content.
  {"ai_summary": "Seven years of real-time object detection at Mobileye, all of it
   automotive video rather than sport. The vision work transfers on paper; whether
   the domain does is the open question.",
   "ai_probes": "Which parts of the automotive tracking stack survive a move to
   broadcast footage? Has she trained on anything with a crowd in frame?"}

Respond with ONLY: {"ai_summary": "...", "ai_probes": "..."}
No markdown fences, no commentary."""


def build_client():
    """Same auth/package checks as build_aliases.py's call_model, factored out
    here so main.py can construct one client and hand it to apply_llm_briefs for
    every candidate in the shortlist, rather than re-checking per call.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

    try:
        import anthropic
    except ImportError:
        sys.exit(
            "ERROR: the 'anthropic' package is not installed. Install it with:\n"
            "  pip3 install anthropic --break-system-packages"
        )

    return anthropic.Anthropic(api_key=api_key)


def _build_prompt(job_summary: dict, candidate: dict) -> str:
    prompt = PROMPT_TEMPLATE.replace(
        "__JOB_SUMMARY__", json.dumps(job_summary, ensure_ascii=False)
    )
    return prompt.replace("__CANDIDATE__", json.dumps(candidate, ensure_ascii=False))


def _parse_response(raw_text: str):
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        return None

    summary = parsed.get("ai_summary")
    probes = parsed.get("ai_probes")
    if not isinstance(summary, str) or not isinstance(probes, str):
        return None
    if not summary.strip() or not probes.strip():
        return None

    return {"ai_summary": summary.strip(), "ai_probes": probes.strip()}


SHORTLIST_PROMPT_TEMPLATE = """Write two sentences that characterize this shortlist as a group, for
a recruiter about to start calling down it.

THE ROLE
__JOB_SUMMARY__

THE SHORTLIST — the top candidates, in rank order, each with title, company, years of experience,
industry, matched skills, missing skills and match score. Nothing outside this is known.
__SHORTLIST__

Say what kind of list this is and name the one thing a recruiter should know before starting to
call — a pattern across candidates, a shared strength, a shared risk.

RULES
1. Two sentences, plain prose, no bullet points, no candidate names.
2. Use only what's in the shortlist above — no inferred trend the data doesn't show.
3. No counts, no percentages, no numbers of any kind. Everything numeric here is already visible
   on screen — the stat tiles show how many are scored, how many clear 70, how many have a
   referral path; every card already shows its own score and its own skill lists. Restating any
   of that wastes both sentences. Say something only a reader of the whole list, not the tiles or
   a single card, would notice.

Respond with ONLY the two sentences. No markdown, no quotation marks, no commentary."""


def _build_shortlist_prompt(job_summary: dict, shortlist: list) -> str:
    prompt = SHORTLIST_PROMPT_TEMPLATE.replace(
        "__JOB_SUMMARY__", json.dumps(job_summary, ensure_ascii=False)
    )
    return prompt.replace("__SHORTLIST__", json.dumps(shortlist, ensure_ascii=False))


def call_shortlist_summary(client, job_summary: dict, shortlist: list):
    """One call, one job — the shortlist-summary counterpart to
    call_candidate_brief. Returns the two-sentence summary string on a
    well-formed response, None on any failure (empty text or an API error).
    None is a normal outcome: the caller (output.apply_llm_job_summary) falls
    back to the deterministic build_job_summary_line instead of failing the run.
    """
    prompt = _build_shortlist_prompt(job_summary, shortlist)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
        return text or None
    except Exception:
        return None


def call_candidate_brief(client, job_summary: dict, candidate: dict):
    """One call, one candidate. Returns {"ai_summary": str, "ai_probes": str} on a
    well-formed response, None on any failure — bad JSON, a missing/wrong-typed
    key, or an API error. None is a normal outcome here, not an exception: a
    shortlist candidate falling back to the deterministic template beats an
    otherwise-complete match run crashing on one bad model response.
    """
    prompt = _build_prompt(job_summary, candidate)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_response(response.content[0].text)
    except Exception:
        return None
