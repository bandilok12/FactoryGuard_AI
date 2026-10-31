"""
GenAI features built into the workflow:

1. Troubleshooting Copilot  (RAG over maintenance manuals + live machine context)  -> technicians
2. Shift Performance Report (LLM narrative over computed KPIs)                       -> plant head
3. Smart Ticket Triage      (operator free text -> structured ticket JSON)           -> operators
4. Quality RCA & CAPA       (failed batch + process params + ML output -> RCA)       -> plant head / QA

Each function returns a dict that always contains `provider` so the UI can show
whether a live LLM or the offline fallback answered.
"""
import json
import re

from . import llm, rag

# --------------------------------------------------------------------------- prompts
TROUBLESHOOT_SYSTEM = """You are FactoryGuard Copilot, a senior maintenance engineer for a pharmaceutical
packaging plant (blister packers, cartoners, strip packers, labellers, filler-cappers, checkweighers).
Answer the technician's question using ONLY the manual excerpts and machine context provided.
Rules:
- Start with the most likely cause given the live sensor data, then give numbered, practical steps.
- Quote exact values from the manuals (temperatures, pressures, torque, part numbers).
- Always include the relevant safety step (LOTO, cool-down, bleed air) before hands-on work.
- If GMP line clearance is needed after the repair, say so.
- Cite sources inline like [Manual: <title> § <section>].
- If the manuals do not cover the question, say so plainly and suggest escalation. Never invent part numbers.
- Keep it under 250 words, use short markdown headings/bullets."""

SHIFT_REPORT_SYSTEM = """You are an operations analyst writing the end-of-shift performance report for the
plant head of a pharmaceutical packaging unit. You receive computed KPIs as JSON. Write a concise
markdown report with these sections:
## Executive summary (3 bullet points, lead with OEE and the biggest loss)
## Production & OEE
## Downtime (top reasons and machines, minutes)
## Quality (rejects, failed batches, defect types)
## Maintenance & machine health (open tickets, anomalies, high failure-risk machines)
## Recommended actions for next shift (max 5, specific: machine code + action + owner role)
Use ONLY numbers present in the JSON; do not invent data. Benchmarks: world-class OEE 85%, typical 60%."""

TRIAGE_SYSTEM = """You are a maintenance planner triaging a breakdown/issue reported by a machine operator in a
pharmaceutical packaging plant. Convert the free-text report into a structured maintenance ticket.
Return ONLY a JSON object with keys:
 "title": short ticket title (max 80 chars),
 "category": one of ["mechanical","electrical","pneumatic","sensor","software","other"],
 "priority": one of ["low","medium","high","critical"]  (critical = safety risk or line stopped on criticality-A machine),
 "probable_causes": list of up to 3 strings,
 "first_checks": list of up to 4 short steps for the technician,
 "suggested_part_nos": list of part_no strings chosen ONLY from the provided spare-parts list (may be empty),
 "safety_note": one sentence,
 "reasoning": one sentence explaining the priority."""

QUALITY_RCA_SYSTEM = """You are a QA engineer in a GMP pharmaceutical packaging plant. A batch inspection result,
process parameters, machine sensor summary and an ML defect-risk assessment are given as JSON.
Write a short root-cause analysis in markdown:
## Problem statement
## Probable root causes (ranked, link each to a parameter value from the data)
## Containment (what to do with this batch now)
## CAPA (corrective & preventive actions with owner role)
Be specific and use only the data provided. Max 220 words."""


def _log_payload(feature, prompt, text, provider, latency):
    return {"feature": feature, "prompt": prompt[:6000], "response": text[:8000],
            "provider": provider, "latency_ms": latency}


# --------------------------------------------------------------------------- 1. troubleshooting copilot
def troubleshoot(question: str, machine: dict | None, sensor_ctx: dict | None, recent_tickets: list,
                 history: list | None = None):
    mtype = machine.get("machine_type") if machine else None
    hits = rag.search(question + " " + " ".join((sensor_ctx or {}).get("flags", [])), mtype, k=4)
    excerpts = "\n\n".join(f"[Manual: {h['manual']} § {h['section']}]\n{h['text']}" for h in hits)
    ctx = {"machine": machine, "latest_sensors": sensor_ctx, "recent_tickets_on_this_machine": recent_tickets}
    user = (f"Technician question: {question}\n\nMachine context (JSON):\n{json.dumps(ctx, default=str)}\n\n"
            f"Manual excerpts:\n{excerpts or '(no relevant excerpts found)'}")

    def offline():
        lines = []
        if machine:
            lines.append(f"**Machine:** {machine.get('code')} — {machine.get('name')} ({mtype})")
        if sensor_ctx and sensor_ctx.get("flags"):
            lines.append("**Live data flags:** " + "; ".join(sensor_ctx["flags"]))
        if not hits:
            lines.append("\nThe manuals in the library do not cover this question. Escalate to the "
                         "maintenance engineer / OEM service.")
            return "\n".join(lines)
        lines.append("\n### Most relevant manual guidance")
        for h in hits[:3]:
            body = [l.strip() for l in h["text"].splitlines()[1:] if l.strip()]
            steps = [l for l in body if re.match(r"^(\d+\.|-)", l)] or body
            lines.append(f"\n**{h['section']}**  _[Manual: {h['manual']}]_")
            for s in steps[:6]:
                lines.append(s if re.match(r"^(\d+\.|-)", s) else f"- {s}")
        lines.append("\n### Safety\n- Apply LOTO, bleed compressed air and let heaters cool below 50 °C "
                     "before hands-on work. Get QA line clearance after work in product-contact areas.")
        if recent_tickets:
            lines.append("\n### History on this machine")
            for t in recent_tickets[:3]:
                lines.append(f"- {t['ticket_no']}: {t['title']} — root cause: {t.get('root_cause') or 'n/a'}")
        lines.append("\n_Offline mode: answer assembled from retrieved manual sections. "
                     "Configure an LLM API key for a synthesized answer._")
        return "\n".join(lines)

    text, provider, ms = llm.generate(TROUBLESHOOT_SYSTEM, user, history=history, fallback=offline)
    sources = [{"manual": h["manual"], "section": h["section"], "score": h["score"], "file": h["file"]}
               for h in hits]
    return {"answer": text, "sources": sources, "provider": provider, "latency_ms": ms,
            "_log": _log_payload("troubleshoot", user, text, provider, ms)}


# --------------------------------------------------------------------------- 2. shift report
def shift_report(kpis: dict):
    user = "Shift KPI data (JSON):\n" + json.dumps(kpis, default=str, indent=1)

    def offline():
        o = kpis.get("oee", {})
        p = kpis.get("production", {})
        d = kpis.get("downtime", {})
        q = kpis.get("quality", {})
        m = kpis.get("maintenance", {})
        top_dt = d.get("by_reason", [])[:3]
        worst = d.get("by_machine", [])[:3]
        risky = m.get("high_risk_machines", [])[:3]
        L = [f"# Shift Performance Report — {kpis.get('date')} / {kpis.get('shift')}", "",
             "## Executive summary",
             f"- Plant OEE **{o.get('oee', 0)}%** (A {o.get('availability', 0)}% × P {o.get('performance', 0)}% × "
             f"Q {o.get('quality', 0)}%) vs world-class 85%.",
             f"- Biggest loss: **{top_dt[0]['reason'].replace('_', ' ') if top_dt else 'none'}** "
             f"({top_dt[0]['minutes'] if top_dt else 0} min downtime)." if top_dt else "- No downtime recorded.",
             f"- {q.get('failed_batches', 0)} batch(es) failed inspection; reject rate {p.get('reject_rate', 0)}%.",
             "", "## Production & OEE",
             f"- Planned {p.get('planned', 0):,} units, produced {p.get('produced', 0):,} "
             f"({p.get('attainment', 0)}% attainment), rejected {p.get('rejected', 0):,}.",
             "", "## Downtime",
             f"- Total {d.get('total_minutes', 0)} min across {d.get('events', 0)} events."]
        L += [f"- {r['reason'].replace('_', ' ').capitalize()}: {r['minutes']} min" for r in top_dt]
        L += [f"- Worst machine: {w['machine']} — {w['minutes']} min" for w in worst[:2]]
        L += ["", "## Quality", f"- Inspections: {q.get('inspections', 0)}, failed: {q.get('failed_batches', 0)}, "
                                 f"defect rate {q.get('defect_rate', 0)}%."]
        L += [f"- {t['type'].replace('_', ' ').capitalize()}: {t['count']} defects" for t in q.get("by_type", [])[:3]]
        L += ["", "## Maintenance & machine health",
              f"- Open tickets: {m.get('open_tickets', 0)}, anomalies flagged: {m.get('anomalies', 0)}."]
        L += [f"- {r['code']} failure risk {round(r['risk'] * 100)}% ({r.get('mode', '')})" for r in risky]
        L += ["", "## Recommended actions for next shift"]
        acts = []
        for r in risky:
            acts.append(f"Technician: inspect **{r['code']}** for {r.get('mode') or 'degradation'} before shift start.")
        if top_dt:
            acts.append(f"Plant head: review root cause of '{top_dt[0]['reason']}' losses with maintenance.")
        if q.get("failed_batches"):
            acts.append("QA: complete RCA/CAPA for failed batches before release.")
        if kpis.get("spares_low"):
            acts.append(f"Stores: raise PO for {len(kpis['spares_low'])} spare part(s) below reorder level.")
        acts.append("Operators: log sensor readings every 2 hours and report abnormal noise immediately.")
        L += [f"{i + 1}. {a}" for i, a in enumerate(acts[:5])]
        L += ["", "_Generated in offline mode from computed KPIs._"]
        return "\n".join(L)

    text, provider, ms = llm.generate(SHIFT_REPORT_SYSTEM, user, temperature=0.2, fallback=offline)
    return {"report": text, "provider": provider, "latency_ms": ms,
            "_log": _log_payload("shift_report", user, text, provider, ms)}


# --------------------------------------------------------------------------- 3. ticket triage
# pattern, category, priority, probable cause, spare-part keywords
KEYWORDS = [
    (r"smoke|fire|spark|burn|shock", "electrical", "critical", "Electrical fault / insulation failure", ["relay"]),
    (r"vibrat|noise|rattl|knock|grind|bearing|shak", "mechanical", "high", "Bearing / drive-train wear",
     ["bearing", "grease", "coupling", "belt"]),
    (r"air|pneumat|pressure|hiss|cylinder|valve", "pneumatic", "high", "Pneumatic leak or regulator drift",
     ["seal kit", "fitting", "solenoid", "filter element"]),
    (r"hot|heat|temp|overheat|heater|thermo", "electrical", "high", "Heater / temperature control fault",
     ["heater", "thermocouple", "relay"]),
    (r"jam|stuck|block|chok", "mechanical", "medium", "Product or carton jam", ["suction cup", "sensor"]),
    (r"seal|leak test|wrinkl", "mechanical", "high", "Sealing station out of spec", ["thermocouple", "sealing plate", "seal kit"]),
    (r"camera|sensor|vision|ocr|photo", "sensor", "medium", "Sensor / vision system fault", ["sensor"]),
    (r"print|label|ribbon", "mechanical", "medium", "Printing / labelling unit issue", ["printhead", "ribbon"]),
    (r"underfill|overfill|fill|dose", "mechanical", "medium", "Dosing / piston seal wear", ["o-ring", "piston"]),
    (r"cut|blade|punch|burr|perforat", "mechanical", "medium", "Cutting tool worn", ["punch", "blade"]),
    (r"overload|trip|motor", "electrical", "high", "Motor overload / drive trip", ["bearing", "coupling"]),
    (r"plc|hmi|software|screen|program", "software", "medium", "PLC/HMI software issue", []),
]


def triage(description: str, machine: dict, sensor_ctx: dict | None, parts: list[dict]):
    parts_txt = "\n".join(f"- {p['part_no']}: {p['name']} (stock {p['quantity']})" for p in parts[:40])
    hits = rag.search(description, machine.get("machine_type"), k=2)
    user = (f"Operator report: \"{description}\"\nMachine: {json.dumps(machine, default=str)}\n"
            f"Latest sensor context: {json.dumps(sensor_ctx or {}, default=str)}\n"
            f"Available spare parts:\n{parts_txt}\n\nRelevant manual excerpts:\n" +
            "\n".join(h["text"][:700] for h in hits))

    def offline():
        d = description.lower()
        cat, pri, causes, hints = "other", "medium", [], []
        for pat, c, p, cause, kw in KEYWORDS:
            if re.search(pat, d):
                if cat == "other":
                    cat, pri = c, p
                causes.append(cause)
                hints += kw
        if re.search(r"stop|down|breakdown|not running|halt", d):
            pri = "critical" if machine.get("criticality") == "A" else "high"
        if sensor_ctx and sensor_ctx.get("flags") and pri in ("low", "medium"):
            pri = "high"
        mtype = (machine.get("machine_type") or "").lower()
        compatible = [p for p in parts if not p.get("compatible_types") or "all" in p["compatible_types"].lower()
                      or mtype in p["compatible_types"].lower()]
        sugg = []
        for h in hints:   # ordered by most likely cause first
            for p in compatible:
                if h in p["name"].lower() and p["part_no"] not in sugg:
                    sugg.append(p["part_no"])
                    break
        sugg = sugg[:3]
        checks = []
        for h in hits[:1]:
            checks = [re.sub(r"^\d+\.\s*", "", l.strip()) for l in h["text"].splitlines()
                      if re.match(r"^\d+\.", l.strip())][:4]
        title = description.strip().split(".")[0][:70]
        return json.dumps({
            "title": f"{machine.get('code')}: {title}",
            "category": cat, "priority": pri,
            "probable_causes": causes[:3] or ["Needs technician diagnosis"],
            "first_checks": checks or ["Inspect machine with LOTO applied", "Check alarm history on HMI"],
            "suggested_part_nos": sugg,
            "safety_note": "Apply LOTO and bleed compressed air before opening guards.",
            "reasoning": f"Keyword analysis ({cat}); machine criticality {machine.get('criticality')}.",
        })

    text, provider, ms = llm.generate(TRIAGE_SYSTEM, user, temperature=0.1, json_mode=True, fallback=offline)
    data = llm.parse_json(text) or llm.parse_json(offline()) or {}
    # sanitize model output
    valid_parts = {p["part_no"] for p in parts}
    data["suggested_part_nos"] = [p for p in data.get("suggested_part_nos", []) if p in valid_parts]
    if data.get("category") not in ("mechanical", "electrical", "pneumatic", "sensor", "software", "other"):
        data["category"] = "other"
    if data.get("priority") not in ("low", "medium", "high", "critical"):
        data["priority"] = "medium"
    data["title"] = str(data.get("title") or description[:80])[:200]
    data["provider"] = provider
    data["_log"] = _log_payload("ticket_triage", user, text, provider, ms)
    return data


# --------------------------------------------------------------------------- 4. quality RCA
def quality_rca(payload: dict):
    user = "Batch data (JSON):\n" + json.dumps(payload, default=str, indent=1)

    def offline():
        insp = payload.get("inspection", {})
        ml = payload.get("ml_assessment", {})
        L = ["## Problem statement",
             f"Batch **{insp.get('batch_no')}** on {payload.get('machine', {}).get('code')}: "
             f"{insp.get('defects_found')} defects in {insp.get('sample_size')} samples "
             f"({insp.get('defect_type')}), result **{insp.get('result')}**.",
             "", "## Probable root causes"]
        oos = ml.get("out_of_spec", [])
        if oos:
            for i, o in enumerate(oos, 1):
                L.append(f"{i}. {o['parameter']} = {o['value']} (optimal {o['optimal']})")
        else:
            L.append("1. Process parameters within spec — check material (foil/PVC lot) and machine condition.")
        s = payload.get("sensor_summary") or {}
        if s.get("anomalies"):
            L.append(f"{len(oos) + 1}. {s['anomalies']} sensor anomalies on the machine during the shift.")
        L += ["", "## Containment",
              "- Quarantine the batch; perform 100% visual + leak test on retained samples.",
              "- Segregate pass/fail lots; do not release until QA disposition.",
              "", "## CAPA"]
        L += [f"- {o['action']} (Owner: Technician)" for o in oos[:3]]
        L += ["- Add the parameter check to the start-of-shift checklist (Owner: Plant head)",
              "- Re-train operators on in-process checks (Owner: QA)",
              "", "_Generated in offline mode._"]
        return "\n".join(L)

    text, provider, ms = llm.generate(QUALITY_RCA_SYSTEM, user, temperature=0.2, fallback=offline)
    return {"analysis": text, "provider": provider, "latency_ms": ms,
            "_log": _log_payload("quality_rca", user, text, provider, ms)}
