# Alias generation log

Evidence of a real run of `pipeline/build_aliases.py` against a live model
(`claude-sonnet-4-6`), captured in full below. This is the only script in the repo that calls a
model — see the README's ["Where the model is used"](../README.md#where-the-model-is-used)
section and `SPEC.md` §8.

Run date: 2026-08-22. Command: `python pipeline/build_aliases.py`. Exit code: `0`.

## Confirming the prompt is printed verbatim

`main()` prints the exact string returned by `build_prompt()` before ever calling the model
(`pipeline/build_aliases.py:177-182`), and `build_prompt()` is a single f-string literal
(`pipeline/build_aliases.py:57-90`) — not text assembled from fragments at runtime. What follows is
that printed block, copied unedited from this run's stdout.

```
================================================================================
PROMPT SENT TO MODEL:
================================================================================
You are building a skill-alias dictionary for a recruiting matching pipeline.

Canonical vocabulary (the exact skill names used by job postings):
[
  "API Products",
  "AWS",
  "Airflow",
  "Broadcast tech",
  "Computer Vision",
  "Docker",
  "FFmpeg",
  "Go",
  "Kafka",
  "Microservices",
  "OTT platforms",
  "Object Detection",
  "Product Management",
  "PyTorch",
  "Python",
  "REST APIs",
  "Real-time processing",
  "Redis",
  "SQL",
  "Spark",
  "Sports Analytics",
  "Sports domain knowledge",
  "Stakeholder Management",
  "Terraform",
  "Video streaming",
  "dbt"
]

Candidate skill spellings that need to be mapped onto the canonical vocabulary (these come from
LinkedIn profiles and may use different casing, abbreviations, synonyms, or related terminology):
[... 116 candidate spellings, e.g. "5G", "APIs", "Deep Learning", "Event Detection", "OpenCV",
"PostgreSQL", "Sports CV", "YOLO" — full list is data/linkedin_profiles.csv top_skills minus the
canonical vocabulary above, sorted]

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

Example shape: {"ml": "Machine Learning", "cv": "Computer Vision"}
================================================================================
```

(The candidate list is elided above to keep this log readable; it's reproduced in full by simply
running the script, since it's computed from the source CSVs, not hand-maintained.)

## This run's result

```
================================================================================
SUMMARY
================================================================================
Existing aliases before: 49
Aliases added: 3
Rejected by validation: 3
Rejection reasons:
  - 'real-time processing' -> 'Real-time processing': key maps to itself
  - 'video streaming' -> 'Video streaming': key maps to itself
  - 'object detection' -> 'Object Detection': key maps to itself

Total aliases after merge: 52

Added entries:
  'otт' -> 'OTT platforms'
  'broadcast systems' -> 'Broadcast tech'
  'broadcast equipment' -> 'Broadcast tech'
```

`data/skill_aliases.json` went from 49 to 52 entries as a result.

**Rejected (3).** All three are `validate_mapping`'s self-mapping guard
(`pipeline/build_aliases.py:143-145`): the model proposed lowercasing a candidate spelling that was
already identical to its canonical target (e.g. `"real-time processing"` → `"Real-time
processing"`), which carries no information and is dropped rather than stored.

**Added (3).** `broadcast systems` and `broadcast equipment` both map to `Broadcast tech` — two new
resolvable candidate-side spellings. The third, `otт -> OTT platforms`, is worth flagging on its own:
the model returned the key with a Cyrillic `т` (U+0442) in place of the Latin `t`, so it landed in
`skill_aliases.json` as a distinct string from the already-present `ott -> OTT platforms` entry
rather than as a duplicate. It's harmless — `normalize_skills` only checks it against exact
candidate-side strings, and no candidate in the current data happens to carry that homoglyph — but
it's a real example of why `resolve_unknown_alias`'s docstring (`pipeline/enrich.py:123-130`)
describes the model as caching into the dictionary rather than being trusted unchecked: a
production version of this pipeline would want to normalize non-ASCII look-alikes before writing a
key, which this offline generator does not currently do.

## What this run did not change

`sports cv -> Computer Vision` and `event detection -> Object Detection` — the two entries behind
the Marcus Reid scoring effect described in the README — were already present in
`data/skill_aliases.json` going into this run (part of the "49 existing" count above, from an
earlier generation run) and were left untouched, since existing entries always win over a fresh
model proposal (`merge_aliases`, `pipeline/build_aliases.py:151-165`).
