# Reference documentation

Detail behind [`DESIGN.md`](../../DESIGN.md). **Not required reading** — the design document is the
deliverable; these two expand it without repeating it.

| File | Holds |
| :---- | :---- |
| [`SPEC.md`](SPEC.md) | **The implementation contract.** System invariants, both flow diagrams, exact rules for all seven scoring components, every output column, the ER data model, module map, presentation contract, the per-step integration surface, failure modes. |
| [`alias_generation_log.md`](alias_generation_log.md) | Full transcript of the one `build_aliases.py` run that generated `data/skill_aliases.json`. |

Pipeline docstrings cite these by path — `docs/reference/SPEC.md §A6` for a Flow A step, `§B3` for a
scoring component.
