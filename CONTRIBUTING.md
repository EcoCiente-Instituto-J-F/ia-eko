# Contributing to EcoCiente IA

Thanks for your interest in improving EcoCiente IA. This guide covers how to set up the project,
how to test a change and how to open a pull request.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). To report a
security problem, **do not** open an issue — follow [SECURITY.md](SECURITY.md).

## Language

The product is Brazilian: identifiers, prompts, API fields and most of `docs/` are in Portuguese.
Issues and pull requests are welcome in Portuguese or English. Keep new code consistent with the
file you are editing (Portuguese domain names such as `condominio_id` stay as they are).

## Setting up

You need Python 3.11 or later. The test suite needs **no** database, LLM or network access.

```bash
git clone https://github.com/EcoCiente-Instituto-J-F/ia-eko.git
cd ia-eko
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # requirements-optional.txt for Groq/Gemini/DeepSeek
cp .env.example .env             # Windows: copy .env.example .env
```

On Windows, also run `pip install tzdata` — without it `zoneinfo` cannot find
`America/Sao_Paulo` and some tests fail.

To run the API without any external service:

```bash
LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false \
  python -m uvicorn src.api.main:app --reload
```

See the [README](README.md) for running against real databases and a real LLM.

## Testing your change

Run the same command the CI runs:

```bash
LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false \
  RUN_INTEGRATION_TESTS=false python -m pytest -q
```

- Every change needs tests that fail without it. Bug fixes start with a test that reproduces
  the bug.
- Tests must not call paid APIs or the network: use `LLM_PROVIDER=mock`, or replace
  `agents.invoke` / `agents.run_analytics` / `agents.invoke_with_tools` with scripted functions
  (see `tests/test_output_parsing.py` and `tests/test_judge_evidence.py`).
- Integration tests in `tests/integration/` only run with `RUN_INTEGRATION_TESTS=true` and
  `APP_ENV=test`. Never point them at production databases.
- If you change the orchestrator prompt or the routing, run the routing evals and report the
  accuracy in the pull request:

  ```bash
  python -m evals.routing.run --min-accuracy 0.85
  ```

## Project rules

These keep the system safe and honest. A pull request that breaks one of them will be asked to
change.

- **Authorization lives in `src/security/policies.py`.** Do not check profiles ad hoc in routes
  or agents.
- **Every new agent tool needs an entry in `TOOL_ACTIONS`** (`src/security/tool_scope.py`).
  A test fails if a tool has no policy, and a tool without one is denied at runtime. Tools must
  not trust `usuario_id` or `condominio_id` coming from the LLM.
- **Prompts live in `src/prompts/`**, not inside agent or graph code. A prompt must only mention
  tools and arguments that exist.
- **No invented numbers.** Prices, premises and measurements come from environment variables or
  real measurements, and are documented. When data is missing, return `null`, not a guess.
- **No real personal data** in tests, fixtures, examples or issues. Use fake names and ids.
- **No secrets in the repository.** `.env` is ignored; add new variables to `.env.example`
  (with a safe default) and to `src/core/config.py`. A test checks that both stay in sync.
- **No new dependencies without discussion.** Open an issue first and explain why the standard
  library or an installed package is not enough.
- Configuration is read only through `Settings` (`src/core/config.py`); do not call
  `os.getenv` elsewhere.

## Pull requests

1. Create a branch from `main` named after the change, for example `feat/long-term-memory` or
   `fix/judge-evidence`.
2. Make focused commits. We use short prefixes in the commit subject: `feat:`, `fix:`,
   `chore:`, `docs:`, `test:`, `refactor:`.
3. Run the test suite locally.
4. Open a pull request against `main`. Describe what changed, why, and how you tested it.
   Mention any new environment variable, migration or behavior change.
5. The CI (`.github/workflows/ci.yml`) must pass. For branches inside this repository, a bot
   (`.github/workflows/pr-bot.yml`) drafts the pull-request description; review and edit it.
   Pull requests from forks do not get the bot.
6. A maintainer reviews the change. Address the comments with new commits; we squash or merge
   when it is approved.

## Reporting bugs and proposing features

Open a GitHub issue with:

- what you expected and what happened;
- the steps, request or chat message that reproduces it (with fake data);
- your configuration (`LLM_PROVIDER`, `STORAGE_MODE`, `FAQ_BACKEND`) and the commit you used;
- for wrong LLM answers, the `agents_called` and `judge` fields of the response help a lot.

For a new feature, explain the problem it solves for residents, building managers or
cooperatives before proposing a solution.
