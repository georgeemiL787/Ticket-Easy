# Automatic checks (CI)

Three workflows in `.github/workflows/`. Every command in them can be run on your own computer from `team_b/` (or
`dashboard/`), so a red check can always be reproduced.

| Workflow | When | Jobs (the check names GitHub shows) |
|---|---|---|
| `ci.yml` | every pull request, and every push to `main` and `team-b` | **Lint and types** (`ruff check`, `ruff format --check`, `mypy`) · **Tests (memory store)** and **Tests (sqlite store)** (`pytest -q` with `TEAM_B_STORE`) · **Scenario report** (`python scripts/scenario_report.py`, the table goes into the job summary, red if a scenario fails) · **JSON Schemas are up to date** (export again, `git diff --exit-code contracts/schemas`) · **Dashboard (types, tests, build)** (`npm ci`, `npm run typecheck`, `npm test`, `npm run build`) · **Docker image builds** (builds `team_b/Dockerfile`; says so and passes while there is none) |
| `safety.yml` | pull requests that touch `team_b/src`, `fixtures`, `config`, `data`, `scenarios` or the safety tests | **Adversarial suite and property test (5 seeds)**: `pytest tests/adversarial` (when it has tests) and the property test with its four fixed seeds plus a fifth (`TEAM_B_EXTRA_SEEDS=424242`), and the leak scan. All must pass: there is no percentage to meet |
| `eval.yml` | every Monday 05:00 UTC, and by hand ("Run workflow", with an optional AI-model run) | **Evaluate**: `python -m team_b eval --set eval/conversations`, the table in the job summary, the report as the `evaluation-report` artifact |

pip and npm downloads are cached (`actions/setup-python` and `actions/setup-node` with `cache:`).

## What a person must switch on in GitHub (CI cannot do it by itself)

Settings, Branches, add a protection rule for `main` and `team-b`:

1. **Require a pull request before merging**, with **Require approvals: 2**.
2. **Require review from Code Owners**. `.github/CODEOWNERS` names the files that need it: `brain/actions.py`, `domain/actions.py`,
   `domain/trace.py`, `adapters/standins/rule_checker.py`, and `.github/`. With 2 approvals required, those changes cannot merge
   on one person's say-so.
3. **Require status checks to pass before merging**, and pick these checks: `Lint and types`, `Tests (memory store)`,
   `Tests (sqlite store)`, `Scenario report`, `JSON Schemas are up to date`, `Dashboard (types, tests, build)`,
   `Docker image builds`, and `Adversarial suite and property test (5 seeds)`.
   (A path-filtered check such as the safety one only runs when its paths change; GitHub treats a check that did not run as not
   required, so it only blocks pull requests that touch the brain.)
4. Optional: **Do not allow bypassing the above settings**, and **Require branches to be up to date before merging**.
5. For the AI-model run of `eval.yml`: add the repository secret `OPENROUTER_API_KEY`.

If the two people are the only owners of the repository, keep the approvals at 2 only if both can review each other's
work; otherwise set approvals to 1 and keep "Require review from Code Owners" on.
