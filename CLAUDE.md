# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 0. Read HANDOFF.md first

**Before touching this repo, read [`HANDOFF.md`](HANDOFF.md).** It carries the
current branch state, the constraints that are not obvious from the code
(`app/repos/models.py` is frozen; all LLM calls must go through
`app/services/provider_factory.py`; user-facing error messages must be in
Chinese — the last two are enforced by `tests/architecture/`), a code map, and
the prioritised backlog. Section 0 is a 60-second version.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```


Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

### 4.1 Prove the test can fail

A test that passes both with and without the bug is worth nothing. Before
claiming a regression is covered:

1. Write the test. **Run it and watch it fail** for the expected reason.
2. Fix the code. Run it again — it should pass.
3. If a test was written after the fix, **temporarily reintroduce the bug** and
   confirm the test goes red.

This is not ceremony. Two real cases from this repo:

- A browser test for the ER diagram passed even with the bug reintroduced,
  because the seeded diagram had no relationship line — and the bug lived in
  mermaid's measurement of relationship paths. The fixture was too simple to
  reproduce it.
- A "reintroduce the bug" experiment silently did nothing because the string
  replacement didn't match. The test looked like a guard; it guarded nothing.

Always inspect the file after reintroducing a bug, before trusting the result.

### 4.2 Which layer should catch it

Pick the cheapest layer that can actually see the failure:

| Layer | Catches | Location |
|---|---|---|
| Unit / rules | Pure logic, edge cases | `tests/rules/`, `tests/repos/` |
| API contract | Request/response shapes the frontend depends on | `tests/web/test_contract.py` |
| Architecture | Cross-file conventions nobody owns | `tests/architecture/` |
| Gateway contract | Behaviour under degraded LLM gateways | `tests/gateway/` |
| Browser smoke | "Looks broken" — render failures, layout-dependent bugs | `tests/e2e/` (`-m e2e`) |

Architecture tests exist because some rules have no natural owner: every file
looks correct on its own and the mistake is that nobody remembered a
convention. Add one when a violation would produce a whole class of bugs that
unit tests structurally cannot see.

Browser smoke tests stay narrow on purpose: render success and absence of
"looks broken" markers. Business logic belongs in the far faster API tests.

## 5. Documentation Hygiene

**Every commit that changes behaviour must update the relevant MD files.**

Before committing, check:
- `README.md` — Does the project structure section still match? Do the usage instructions still work?
- `docs/architecture.md` — Does any new module, layer, or data flow need to be reflected?
- Any other `docs/*.md` that describes something you changed.

Rules:
- If you add a new top-level directory or entry point, add it to the README structure tree.
- If you add a new API surface (endpoints, CLI flags, env vars), document it.
- If you restructure how data flows between modules, update the architecture diagram.
- Do **not** update MD files for internal refactors that don't change the observable interface.

The test: Could a new developer read README + architecture.md and understand how to run and extend the system?

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation rather than after mistakes, and documentation never lags behind the code.
