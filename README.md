# Aloy

A personal desktop companion with spoken interaction, coordinated teaching visuals,
persistent context, and scheduled follow-through. The first intended use is a German tutor.

## Status

Foundation only: installable Python package, pinned development dependencies,
engineering rules and an implementation plan. No agent, voice service, scheduler,
model connection or desktop interface is implemented yet.

## Development

Requires Python 3.13. Use standard venv and pip.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -c "import aloy; print(aloy.__file__)"
```

Pytest is installed for meaningful behavioral tests as the runtime is implemented.
There are currently no behavioral tests or working application to launch.
GitHub Actions repeats the environment, lint, format and import checks on pushes and PRs.
No API key is needed for this scaffold. Copy `.env.example` to `.env` only when
configuring a provider. Credentials and personal learner data must never enter Git.

Read [AGENTS.md](AGENTS.md), [engineering rules](docs/ENGINEERING.md),
and the [initial plan](docs/INITIAL_PLAN.md) before making changes.
Local operators may also have an ignored `MEMORY.md` and `.local/` context directory.
