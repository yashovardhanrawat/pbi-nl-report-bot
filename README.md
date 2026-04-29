# PBI Langchain PoC — NL → Power BI Report

A LangGraph-based PoC that takes a natural-language business question
and emits a complete Power BI Project (PBIP) folder you can open in
Power BI Desktop.

It works against a fixed reference semantic model (the EcommerceAnalytics
PBIP shipped under `reference_model/`) and uses Groq Llama 3.3 70B for
both planning and SQL generation.

## How it works

```
NL question
   │
   ▼
[1] Schema loader        ──►  parses reference_model/.../model.bim
   │                            into a compact LLM context
   ▼
[2] Visual planner (LLM) ──►   {visual_type, axes, mode}
   │                              mode = 'existing' or 'custom_sql'
   ▼
[3a] mode=existing       ──►   reuse existing measures & columns
[3b] mode=custom_sql     ──►   generate Fabric T-SQL (LLM)
                                + validate against the warehouse
   ▼
[4] PBIP generator       ──►   reports_out/<slug>_<timestamp>/
                                  GeneratedReport.pbip
                                  GeneratedReport.Report/
                                  GeneratedReport.SemanticModel/
```

The generator never modifies your original model — every run produces a
fresh self-contained PBIP folder.

## Setup

### 1. Python environment

In PyCharm's terminal (your existing `.venv` already exists in
`pbi-langchain-poc/`), run:

```bash
pip install -r requirements.txt
```

`pyodbc` is optional — only used for strict SQL validation against the
Fabric warehouse. If you don't have the **Microsoft ODBC Driver 18 for
SQL Server** installed, the validator will soft-pass and the report
will still be generated (any SQL errors will surface in Power BI
Desktop when you open the file).

### 2. `.env`

Copy the template:

```bash
cp .env.example .env
```

Then fill in `GROQ_API_KEY`. The other values default to your existing
EcommerceAnalytics workspace + dataset and the Fabric warehouse from
the reference model.

### 3. Azure CLI auth (for SQL validation)

```bash
az login
```

Use the same identity that has access to the Fabric workspace.

## Run it

```bash
# one-shot
python -m src.main "What is the total revenue by region last year?"

# interactive
python -m src.main
```

Output:

```
PBIP written to: <path>/reports_out/revenue_by_region_20260429_120030
Open the .pbip file inside that folder with Power BI Desktop.
```

## Dry-run (no LLM)

Useful for sanity-testing the generator without burning Groq tokens:

```bash
python -m scripts.dry_run existing
python -m scripts.dry_run custom
```

## Project layout

```
pbi-langchain-poc/
├── .env / .env.example             config
├── .gitignore
├── requirements.txt
├── README.md
├── src/
│   ├── config.py                   env + paths singleton
│   ├── plan_types.py               pydantic plan dataclasses
│   ├── schema_loader.py            model.bim → SemanticModel + LLM summary
│   ├── visual_planner.py           LLM call: NL → Plan
│   ├── nl_to_sql.py                LLM call: Plan → T-SQL (custom_sql only)
│   ├── sql_validator.py            run SQL against Fabric (pyodbc / soft-pass)
│   ├── pbip_generator.py           write the PBIP folder tree
│   ├── graph.py                    LangGraph wiring
│   └── main.py                     CLI entry
├── scripts/
│   └── dry_run.py                  generator smoke test
├── reference_model/
│   └── EcommerceAnalytics.SemanticModel/
│       └── model.bim               read-only reference
└── reports_out/                    one folder per generated report (gitignored)
```

## Supported visual types (PoC scope)

| visual_type   | When the planner picks it                    |
| ------------- | -------------------------------------------- |
| `cardVisual`  | single KPI / number                          |
| `lineChart`   | trend over time                              |
| `barChart`    | few categorical values, measure on X         |
| `columnChart` | few categorical values, measure on Y         |
| `donutChart`  | share-of-total across <=6 categories         |
| `tableEx`     | row-level details, top-N lists, comparisons  |

Adding more (matrix, gauge, scatter, slicers, filters) is a matter of
extending `_build_visual_json` and the planner's allowed list.

## Known PoC limitations

- The bot generates **one visual per report**. Multi-visual dashboards
  are a future extension.
- Custom-SQL tables are added in **Import** mode for compatibility.
  For DirectLake / DirectQuery, switch the partition `mode` and the
  generated `model.bim` will need a TMSL refresh trigger.
- All custom-SQL columns are typed `string` by default; Power BI
  Desktop will infer correct types on first refresh. To fix at
  generation time, add a type-inference step using the `sample_columns`
  returned by the SQL validator.
- The planner is fixed to the EcommerceAnalytics schema. To target a
  different model, point `REFERENCE_MODEL_PATH` at a different
  `model.bim`.

## GitHub setup (one-time)

In the project root:

```bash
git init
git branch -M main
git add .
git commit -m "initial: NL → PBIP scaffolding"
```

Then in PyCharm: **Git → GitHub → Share Project on GitHub** to push.
Or via CLI:

```bash
git remote add origin https://github.com/<you>/pbi-nl-report-bot.git
git push -u origin main
```

`.env`, `reports_out/`, and `.venv/` are already gitignored.
