"""
End-to-end API test against a running server.
Usage:  python tests/test_api.py [base_url]      (default http://localhost:8000)
"""
import json
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api"
PASS, FAIL = 0, 0


def call(method, path, body=None, token=None, expect=None):
    global PASS, FAIL
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, data = r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        code, data = e.code, json.loads(e.read() or b"null")
    ok = (code == expect) if expect else code < 400
    PASS += ok
    FAIL += not ok
    print(f"{'PASS' if ok else 'FAIL'} {method:6} {path:45} -> {code}" + ("" if ok else f"  {str(data)[:200]}"))
    return data


def login(u, p):
    return call("POST", "/auth/login", {"username": u, "password": p})["access_token"]


admin = login("admin", "Admin@123")
head = login("planthead", "Plant@123")
tech = login("tech1", "Tech@123")
op = login("op01", "Oper@123")

# auth & security
call("POST", "/auth/login", {"username": "admin", "password": "wrong"}, expect=401)
call("GET", "/machines", expect=401)
call("GET", "/auth/me", token=op)
call("POST", "/users", {"username": "x", "full_name": "X", "role": "operator", "password": "abc"}, op, expect=403)
call("POST", "/users", {"username": "newop", "full_name": "New Operator", "role": "operator", "password": "weakpass"}, admin, expect=422)
u = call("POST", "/users", {"username": "newop", "full_name": "New Operator", "role": "operator", "password": "Strong123"}, admin)
call("POST", "/users", {"username": "newop", "full_name": "Dup", "role": "operator", "password": "Strong123"}, admin, expect=409)
call("PATCH", f"/users/{u['id']}", {"phone": "+91 9876543210"}, admin)
call("GET", "/users", token=head)

# machines
ms = call("GET", "/machines", token=op)
m = ms[0]
call("GET", f"/machines/{m['id']}", token=tech)
call("POST", "/machines", {"code": "TST-01", "name": "Test Blister", "machine_type": "Blister Packer",
                           "pressure_min": 7, "pressure_max": 5}, admin, expect=422)
nm = call("POST", "/machines", {"code": "TST-01", "name": "Test Blister", "machine_type": "Blister Packer"}, admin)
call("POST", "/machines", {"code": "TST-02", "name": "x", "machine_type": "y"}, op, expect=403)
call("PATCH", f"/machines/{nm['id']}/status", {"status": "idle"}, tech)
call("DELETE", f"/machines/{nm['id']}", token=admin)

# shifts / roster
shifts = call("GET", "/shifts", token=op)
call("POST", "/roster", {"user_id": u["id"], "shift_id": shifts[0]["id"], "machine_id": m["id"],
                         "work_date": date.today().isoformat()}, head)
call("GET", "/roster", token=op)

# production
today = date.today().isoformat()
call("POST", "/production", {"machine_id": m["id"], "shift_id": shifts[0]["id"], "log_date": today, "product_name": "Test",
                             "batch_no": "BTEST-1", "planned_qty": 1000, "produced_qty": 100, "rejected_qty": 200,
                             "run_time_min": 400}, op, expect=422)
pl = call("POST", "/production", {"machine_id": m["id"], "shift_id": shifts[0]["id"], "log_date": today,
                                  "product_name": "Paracetamol", "batch_no": "BTEST-1", "planned_qty": 40000,
                                  "produced_qty": 38000, "rejected_qty": 300, "planned_time_min": 450,
                                  "run_time_min": 420, "ideal_cycle_sec": 0.6}, op)
print("   OEE of new log:", pl["oee"])
call("POST", "/production", {"machine_id": m["id"], "shift_id": shifts[0]["id"], "log_date": today,
                             "product_name": "Paracetamol", "batch_no": "BTEST-1", "planned_qty": 1, "produced_qty": 1,
                             "run_time_min": 1}, op, expect=409)
call("POST", "/production", {"machine_id": m["id"], "shift_id": 1, "log_date": today, "product_name": "x1",
                             "batch_no": "B2", "planned_qty": 1, "produced_qty": 1, "run_time_min": 1}, tech, expect=403)
call("GET", "/production?limit=5", token=head)

# sensors + AI anomaly / prediction
r = call("POST", "/sensors", {"machine_id": m["id"], "temperature": 92, "vibration": 9.5, "pressure": 3.9, "rpm": 1300,
                              "torque": 70, "tool_wear": 210}, op)
print("   anomaly:", r["anomaly"], "| auto ticket:", r["auto_ticket"])
print("   prediction:", {k: r["prediction"][k] for k in ("probability", "risk_level", "mode_description")})
call("POST", "/sensors", {"machine_id": m["id"], "temperature": 500, "vibration": 1, "pressure": 6, "rpm": 1,
                          "torque": 1, "tool_wear": 1}, op, expect=422)
call("GET", "/sensors/latest", token=op)
call("GET", f"/ai/predict/{m['id']}", token=tech)
fr = call("GET", "/ai/fleet-risk", token=head)
print("   top risk:", [(x["code"], round(x["risk"], 2)) for x in fr[:4]])
call("GET", "/ai/models", token=head)

# maintenance workflow
t = call("POST", "/tickets", {"machine_id": m["id"], "title": "Loud grinding noise from main drive",
                              "description": "Machine vibrating a lot and sealing plate seems hot", "use_ai_triage": True}, op)
print("   triage:", {k: t["ai_triage"].get(k) for k in ("category", "priority", "suggested_part_nos", "provider")})
tid = t["id"]
call("PATCH", f"/tickets/{tid}", {"status": "in_progress"}, op, expect=403)
call("PATCH", f"/tickets/{tid}", {"status": "resolved"}, tech, expect=409)
call("PATCH", f"/tickets/{tid}", {"assigned_to_id": 3}, head)
call("PATCH", f"/tickets/{tid}", {"status": "in_progress", "comment": "Starting work, LOTO applied"}, tech)
parts = call("GET", "/spares", token=tech)
call("POST", f"/tickets/{tid}/parts", {"part_id": parts[0]["id"], "quantity": 999}, tech, expect=409)
call("POST", f"/tickets/{tid}/parts", {"part_id": parts[0]["id"], "quantity": 1}, tech)
call("PATCH", f"/tickets/{tid}", {"status": "resolved"}, tech, expect=422)
call("PATCH", f"/tickets/{tid}", {"status": "resolved", "root_cause": "Bearing worn", "action_taken": "Replaced bearing",
                                  "labour_hours": 1.5}, tech)
call("PATCH", f"/tickets/{tid}", {"status": "closed"}, tech, expect=403)
fin = call("PATCH", f"/tickets/{tid}", {"status": "closed"}, head)
print("   ticket final:", fin["status"], "activities:", len(fin["activities"]), "parts cost:", fin["parts_cost"])

# spares
call("GET", "/spares?low_stock=true", token=tech)
call("GET", "/spares/reorder-report", token=head)
call("POST", f"/spares/{parts[0]['id']}/adjust", {"delta": -100000, "reason": "test"}, tech, expect=409)
call("POST", f"/spares/{parts[0]['id']}/adjust", {"delta": 5, "reason": "GRN 1234"}, tech)
call("POST", "/spares", {"part_no": "TST-P1", "name": "Test part", "category": "Test", "quantity": 1, "reorder_level": 3}, head)

# quality
call("POST", "/quality", {"batch_no": "BTEST-1", "machine_id": m["id"], "sample_size": 100, "defects_found": 120}, op, expect=422)
qi = call("POST", "/quality", {"batch_no": "BTEST-1", "machine_id": m["id"], "production_log_id": pl["id"], "sample_size": 200,
                               "defects_found": 9, "defect_type": "seal_leak", "seal_temp": 168, "seal_pressure": 3.6,
                               "line_speed": 58, "fill_weight_dev": 0.4, "humidity": 61}, op)
print("   inspection:", qi["result"], "ai risk", qi["ai_defect_risk"])
dr = call("POST", "/quality/predict", {"machine_id": m["id"], "seal_temp": 166, "seal_pressure": 3.7, "line_speed": 57,
                                       "fill_weight_dev": 2.1, "humidity": 62}, op)
print("   defect risk:", dr["defect_probability"], dr["likely_defect_type"], dr["recommendation"])

# downtime
now = datetime.now().replace(microsecond=0)
call("POST", "/downtime", {"machine_id": ms[5]["id"], "start_time": (now - timedelta(hours=1)).isoformat(),
                           "end_time": (now - timedelta(hours=2)).isoformat(), "reason_category": "breakdown"}, op, expect=422)
d = call("POST", "/downtime", {"machine_id": ms[5]["id"], "start_time": (now - timedelta(minutes=20)).isoformat(),
                               "reason_category": "material_shortage", "reason_detail": "PVC film awaited"}, op)
call("POST", f"/downtime/{d['id']}/close", {}, op)
call("GET", "/downtime?limit=5", token=head)

# dashboards / OEE / alerts
ov = call("GET", "/dashboard/overview?days=7", token=head)
print("   KPIs:", {k: ov["kpis"][k] for k in ("oee", "availability", "performance", "quality", "mttr_hours", "mtbf_hours")})
call("GET", "/dashboard/overview", token=op, expect=403)
call("GET", "/dashboard/my", token=op)
call("GET", "/dashboard/my", token=tech)
call("GET", "/analytics/oee?group_by=shift", token=head)
call("GET", "/analytics/oee?group_by=bogus", token=head, expect=422)
al = call("GET", "/alerts", token=tech)
if al:
    call("POST", f"/alerts/{al[0]['id']}/ack", token=tech)

# GenAI
gs = call("GET", "/genai/status", token=op)
print("   GenAI provider:", gs["provider"])
call("GET", "/genai/manuals", token=tech)
ts = call("POST", "/genai/troubleshoot", {"question": "Vibration is high on the blister machine, what should I check?",
                                          "machine_id": m["id"]}, tech)
print("   copilot sources:", [s["section"] for s in ts["sources"]])
sr = call("POST", "/genai/shift-report", {"report_date": (date.today() - timedelta(days=1)).isoformat(),
                                          "shift_id": shifts[0]["id"]}, head)
print("   report first line:", sr["report"].splitlines()[0])
call("POST", "/genai/shift-report", {"report_date": today}, op, expect=403)
call("POST", "/genai/triage", {"machine_id": m["id"], "description": "air leaking hiss near sealing cylinder, pressure low"}, op)
rca = call("POST", f"/genai/quality-rca/{qi['id']}", None, head)
print("   RCA head:", rca["analysis"].splitlines()[0])
call("GET", "/genai/history", token=head)

# admin
call("GET", "/settings", token=head)
call("PUT", "/settings/prediction_alert_threshold", {"value": "5"}, admin, expect=422)
call("PUT", "/settings/prediction_alert_threshold", {"value": "0.6"}, admin)
call("GET", "/audit-logs?limit=5", token=admin)
call("GET", "/audit-logs", token=head, expect=403)
call("GET", "/simulator/status", token=op)
call("POST", "/simulator/inject", {"machine_id": ms[10]["id"], "mode": "bearing", "severity": 0.9}, head)
call("POST", "/simulator/stop", token=op, expect=403)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
