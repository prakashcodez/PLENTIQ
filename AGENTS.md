# Project Guidance

Read [AGENTS/PLENTIQ_AGENTS.md](AGENTS/PLENTIQ_AGENTS.md) for the project context and safeguards.

- Keep edits minimal and scoped to the requested task; inspect relevant code first.
- Protect database data. Do not reset, clean, seed, or run setup/cleanup SQL without explicit authorization. Never run `scratch/test_stage3.py` against `plentiq_db`.
- Verify changes with relevant checks and report exactly what was checked; do not claim unverified behavior.
- `app.py` reads required `PLENTIQ_SECRET_KEY` and optional `PLENTIQ_DEMO_MODE`; `app.py` and `db_config.py` load the root `.env` with `python-dotenv` when present. Environment variables may also be set in the process.
