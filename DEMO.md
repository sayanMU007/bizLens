# BizLens: 2½-minute hackathon demo

**Pitch line:** *BizLens doesn't just tell you what happened. It shows you the evidence behind
the answer and what you can do next.*

## Setup (do this once, ~5 min, before you present)

```bash
cp .env.example .env          # then set GROQ_API_KEY in .env
docker compose up --build db backend      # db + backend only (the demo UI is served by Django)
docker compose exec backend python manage.py llm_check      # must print OK
```
Open **http://localhost:8000/**. Click **Use sample sales.csv**, then run the demo question
once as a dry run so the model is warm.

No Docker? `cd backend && pip install -r requirements.txt`, point `POSTGRES_*` at any Postgres,
then `python manage.py migrate && python manage.py runserver`.

## Script

| Time | Do | Say |
|---|---|---|
| 0:00 | Show the empty page | "Every business owner asks *why did sales drop?* Dashboards show *what*. They don't show *why*, and AI chatbots just make up a story." |
| 0:15 | Click **Use sample sales.csv** | "I upload a sales file. BizLens validates it, cleans it, and stores it as Parquet." |
| 0:30 | Click **Analyze** on *Why did revenue decrease last month?* | "I ask in plain English. The AI's only job is to turn that into a *plan*. Code checks the plan, and a SQL engine does every calculation." |
| 0:50 | **Answer** card | "Revenue fell 20.5% from August to September. Notice the badge: every number in this sentence was audited against the evidence. The AI is not allowed to do math." |
| 1:10 | **Evidence**; click a chip in the answer | "Click any claim and it jumps to the exact number behind it: East explains 78% of the drop, and it's a volume problem, not price." |
| 1:35 | **Open SQL**: expand one query, click Copy | "Here's the exact SQL that produced that number. Paste it anywhere and you get the same result. That's traceability." |
| 1:55 | **Reasoning** + *How BizLens got here* | "Plan, check, run, narrate, audit. If the AI had invented a figure, the audit rejects it and we fall back to a plain summary." |
| 2:15 | **Recommended actions** | "And it doesn't stop at diagnosis: prioritized next steps, each tied to the evidence." |
| 2:30 | Type *Forecast revenue for next year* | "And when the data can't answer something, it says so instead of guessing." |

## Why it's trustworthy (judge Q&A)

- **Does the LLM do the math?** No. Vetted analyses run fixed, parameterised SQL in DuckDB.
  The narrative may only quote numbers that exist in the evidence; code rejects anything else.
- **What if no analysis fits?** Validated custom SQL: one read-only SELECT over one table,
  checked with DuckDB's own parser, run in a locked sandbox (no files, no network, 10 s limit).
  It is always labelled as AI-written.
- **Is the answer reproducible?** Yes. Single-threaded DuckDB, and every result carries the
  dataset's SHA-256 and the exact SQL.
- **What leaves the server?** Schema, date range, and the top labels of each text column
  (e.g. region names), never raw rows or query results.
- **Known limits (say them first, it builds trust):** single user and no login (Phase 9);
  filters are equality-only; breakdowns show association, not proven cause.

## If something breaks on stage

- *Groq slow/down:* the page shows the error; say so and show `POST /api/datasets/<id>/analyze/`
  (the vetted engine needs no LLM) in the browsable API.
- Keep a screen recording of one good run as a backup.
