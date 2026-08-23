# Reference documentation

Long-form material behind [`../../DESIGN.md`](../../DESIGN.md). **Not required reading** — the
design document summarizes everything here.

| File | Holds |
| :---- | :---- |
| [`SPEC.md`](SPEC.md) | **What and how.** System shape, both flow diagrams, exact scoring rules for all seven components, column contracts, the ER data model, module map, presentation contract, production topology, failure modes. |
| [`DECISIONS.md`](DECISIONS.md) | **Why.** The seven assumption questions answered one by one, scoring rationale, evidence from the data, the model boundary, open questions. Wins wherever it disagrees with `SPEC.md`. |
| [`alias_generation_log.md`](alias_generation_log.md) | Record of the one `build_aliases.py` run that generated `data/skill_aliases.json`. |

The pipeline docstrings cite these by path — e.g. `docs/reference/SPEC.md §A6` for a Flow A step, or
`§B3` for a scoring component.
