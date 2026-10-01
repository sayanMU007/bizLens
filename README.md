# BizLens
A traceable AI decision engine that turns business data into answers, evidence, and actions.

**Quick start:** copy `.env.example` to `.env`, set `GROQ_API_KEY`, then
`docker compose up --build db backend` and open <http://localhost:8000/>.
Demo script: [DEMO.md](DEMO.md).

## How it works
`question -> AnalysisPlan (LLM, JSON) -> code validation -> vetted analysis | validated custom SQL
 -> DuckDB -> evidence + SQL -> narrative (LLM, audited) -> answer, reasoning, actions`

| Piece | Where |
|---|---|
| LLM provider (Groq, swappable) | `backend/ai/` |
| Upload, validation, Parquet, sandboxed DuckDB | `backend/datasets/` |
| Five vetted, reconciled analyses | `backend/analytics/` |
| Question -> plan -> SQL guard -> run -> audited narrative | `backend/ask/` |
| Demo UI (single HTML page, no build) | `backend/core/static_demo/demo.html` |

API: `POST /api/datasets/` (upload) and `POST /api/datasets/<id>/ask/` with `{"question": "..."}`.
Tests: `cd backend && python manage.py test` (190 tests, no network needed).
CLI: `python manage.py ask_question <dataset_id> "Why did revenue decrease last month?"`.
