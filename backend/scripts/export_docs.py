"""
Generate deliverable artefacts:
  database/schema_sqlite.sql, database/schema_postgresql.sql, database/schema_mysql.sql
  docs/openapi.json, docs/API.md
Run from backend/:  python -m scripts.export_docs
"""
import json
from pathlib import Path

from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import sqlite, postgresql, mysql

from app import models  # noqa: F401  (registers tables)
from app.database import Base
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
(ROOT / "database").mkdir(exist_ok=True)
(ROOT / "docs").mkdir(exist_ok=True)

HEADER = """-- FactoryGuard AI — database schema ({name})
-- Generated from the SQLAlchemy models in backend/app/models.py
-- 17 tables: users, shifts, shift_assignments, machines, production_logs, sensor_readings,
-- maintenance_tickets, ticket_activities, spare_parts, part_usages, quality_inspections,
-- downtime_events, alerts, failure_predictions, settings, audit_logs, genai_logs
-- Load sample data by starting the app once (auto-seed) or: cd backend && python -m app.seed --reset

"""
for name, dialect in (("sqlite", sqlite.dialect()), ("postgresql", postgresql.dialect()), ("mysql", mysql.dialect())):
    out = [HEADER.format(name=name)]
    for t in Base.metadata.sorted_tables:
        out.append(str(CreateTable(t).compile(dialect=dialect)).strip() + ";\n")
        for ix in t.indexes:
            out.append(str(CreateIndex(ix).compile(dialect=dialect)).strip() + ";")
        out.append("")
    (ROOT / "database" / f"schema_{name}.sql").write_text("\n".join(out))

spec = app.openapi()
(ROOT / "docs" / "openapi.json").write_text(json.dumps(spec, indent=2))

# Markdown API reference grouped by tag
ROLE_NOTES = {}
groups = {}
for path, methods in spec["paths"].items():
    for m, op in methods.items():
        groups.setdefault((op.get("tags") or ["Other"])[0], []).append((m.upper(), path, op.get("summary") or op.get("operationId", "")))
lines = ["# FactoryGuard AI — API reference", "",
         "Interactive Swagger UI: **http://localhost:8000/docs** · ReDoc: **/redoc** · raw spec: `docs/openapi.json`.", "",
         "All endpoints are under `/api` and need `Authorization: Bearer <token>` except login and health.",
         "Get a token with `POST /api/auth/login {\"username\":\"admin\",\"password\":\"Admin@123\"}`.", "",
         "Errors use standard codes: 401 not signed in, 403 role not allowed, 404 not found, 409 conflict / invalid workflow "
         "transition, 422 validation error (message lists each field), 423 account locked, 429 too many login attempts.", ""]
for tag, ops in groups.items():
    lines += [f"## {tag}", "", "| Method | Path | Description |", "|---|---|---|"]
    lines += [f"| `{m}` | `{p}` | {s} |" for m, p, s in ops]
    lines.append("")
lines += ["## Role access summary", "",
          "| Area | Admin | Plant head | Technician | Operator |", "|---|---|---|---|---|",
          "| Users, settings, audit log | full | read settings | – | – |",
          "| Machines | full | status | status | read |",
          "| Shifts & roster | full | assign | own roster | own roster |",
          "| Production logs | full | create/edit | read | create / edit own (24 h) |",
          "| Sensor readings | create | read | create | create |",
          "| Tickets | full | assign / verify & close | self-assign, work, resolve, issue parts | raise, comment |",
          "| Spare parts | full | create/edit | adjust stock | read |",
          "| Quality inspections | full | create, RCA | read | create, RCA |",
          "| Downtime | full | full | log/close | log/close |",
          "| Dashboard & OEE analytics | yes | yes | role home | role home |",
          "| GenAI: copilot / triage | yes | yes | yes | yes |",
          "| GenAI: shift report | yes | yes | – | – |",
          "| Simulator control / fault inject | full | inject | – | – |", ""]
(ROOT / "docs" / "API.md").write_text("\n".join(lines))
print("endpoints:", sum(len(v) for v in groups.values()), "tables:", len(Base.metadata.sorted_tables))
