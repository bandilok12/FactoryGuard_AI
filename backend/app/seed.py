"""
Synthetic seed data: 40 machines, 3 shifts, 25 users, 30 days of production / downtime /
quality / maintenance history, spare parts and sensor history. Deterministic (seeded RNG).
No real factory data.

Run manually:  python -m app.seed --reset
"""
import json
import random
import sys
from datetime import datetime, date, timedelta, time

from sqlalchemy.orm import Session

from . import models as M, services
from .database import Base, engine, SessionLocal
from .security import hash_password
from .utils import now_local
from .ml.inference import defect_risk_batch

R = random.Random(2026)

MACHINE_TYPES = [
    # type, prefix, count, rated speed/h, manufacturer, model, power kW, temp_max
    ("Blister Packer", "BP", 10, 6000, "Packtech Systems", "BP-250", 18.5, 75),
    ("Cartoner", "CT", 8, 7200, "Packtech Systems", "CT-120", 7.5, 70),
    ("Strip Packer", "SP", 6, 4000, "Packtech Systems", "SP-4", 11.0, 78),
    ("Labeller", "LB", 6, 12000, "Packtech Systems", "LB-300", 3.0, 65),
    ("Filler-Capper", "FC", 5, 4200, "Packtech Systems", "FC-8", 5.5, 65),
    ("Checkweigher", "CW", 5, 7200, "Packtech Systems", "CW-60", 1.5, 60),
]
PRODUCTS = {
    "Blister Packer": ["Paracetamol 500mg 10x10 Blister", "Amoxicillin 250mg Alu-Alu", "Metformin 500mg Blister"],
    "Cartoner": ["Paracetamol 500mg Carton", "Cetirizine 10mg Carton", "Pantoprazole 40mg Carton"],
    "Strip Packer": ["Azithromycin 500mg Strip", "Ibuprofen 400mg Strip"],
    "Labeller": ["Cough Syrup 100ml Label", "Antacid Suspension 200ml Label"],
    "Filler-Capper": ["Cough Syrup 100ml", "Antacid Suspension 200ml"],
    "Checkweigher": ["Carton weight check", "Shipper weight check"],
}
DEFECT_TYPES = {"Blister Packer": ["seal_leak", "missing_tablet", "crack"], "Strip Packer": ["seal_leak", "crack"],
                "Cartoner": ["label_error", "misprint"], "Labeller": ["misprint", "label_error"],
                "Filler-Capper": ["underfill", "overfill"], "Checkweigher": ["missing_tablet", "underfill"]}
BAD_ACTORS = {"BP-03": 2.6, "CT-02": 2.0, "SP-04": 1.8, "BP-07": 1.5, "FC-02": 1.6}   # downtime multipliers
DT_REASONS = [("breakdown", 0.34), ("changeover", 0.22), ("material_shortage", 0.12), ("planned_maintenance", 0.10),
              ("quality_hold", 0.08), ("power_failure", 0.06), ("no_operator", 0.04), ("other", 0.04)]
DT_DETAIL = {
    "breakdown": ["Main drive bearing noise, machine stopped", "Sealing heater failed (E01)", "Punch station jam",
                  "Pneumatic cylinder leak, low air", "Vacuum pump failure", "Index gearbox overload trip",
                  "Timing belt snapped", "Servo drive fault"],
    "changeover": ["Product changeover — format parts change", "Foil roll change", "Batch changeover & line clearance"],
    "material_shortage": ["PVC film not available", "Cartons short from stores", "Leaflets awaited from QA"],
    "planned_maintenance": ["Monthly PM as per schedule", "Calibration of sealing thermocouple"],
    "quality_hold": ["Leak test failure — QA hold", "OCR misprint investigation"],
    "power_failure": ["Grid power dip, DG changeover", "MCC breaker tripped"],
    "no_operator": ["Operator absent, no relief"],
    "other": ["Housekeeping / cleaning", "Awaiting QA line clearance"],
}
ROOT_CAUSES = [
    ("mechanical", "Main drive bearing worn (vibration 8.4 mm/s)", "Replaced bearing 6205-2RS, re-greased, vibration now 2.6 mm/s", "BRG-6205"),
    ("electrical", "Sealing heater cartridge open circuit", "Replaced heater cartridge and SSR, PID re-tuned", "HTR-CART-1K3"),
    ("pneumatic", "Push-in fitting leak at sealing cylinder", "Replaced fitting and cylinder seal kit, pressure 6.1 bar", "CYL-SK-32"),
    ("mechanical", "Timing belt cracked", "Replaced HTD-8M belt, tension set to spec", "BELT-HTD8M"),
    ("sensor", "Photo sensor misaligned / dirty", "Cleaned and re-taught sensor", "SNS-PHOTO-01"),
    ("mechanical", "Punch & die worn beyond 200 h", "Changed punch & die set", "PD-BP250"),
    ("pneumatic", "FRL filter choked, 0.8 bar drop", "Replaced FRL filter element", "FRL-FLT-05"),
    ("electrical", "Thermocouple loose on sealing plate", "Replaced K-type thermocouple with paste", "TC-K-150"),
]
PARTS = [
    ("BRG-6205", "Deep groove ball bearing 6205-2RS", "Bearings", "Blister Packer,Strip Packer,Cartoner", 14, 6, 20, 420, "SKF India"),
    ("BRG-6206", "Deep groove ball bearing 6206-2RS", "Bearings", "Blister Packer,Cartoner", 4, 6, 20, 510, "SKF India"),
    ("BRG-6204", "Deep groove ball bearing 6204-2RS", "Bearings", "Strip Packer,Labeller", 10, 5, 15, 360, "NBC Bearings"),
    ("HTR-CART-1K3", "Heater cartridge 230V 1.3kW", "Electrical", "Blister Packer,Strip Packer", 3, 4, 10, 2350, "Vizag Electricals"),
    ("TC-K-150", "Thermocouple K-type 150mm", "Electrical", "Blister Packer,Strip Packer", 9, 5, 15, 780, "Vizag Electricals"),
    ("SSR-40A", "Solid state relay 40A", "Electrical", "Blister Packer,Strip Packer,Filler-Capper", 6, 3, 10, 1150, "Vizag Electricals"),
    ("SV-52-24V", "Solenoid valve 5/2 24VDC", "Pneumatics", "Blister Packer,Cartoner,Filler-Capper", 5, 4, 10, 2900, "Festo Distributor Vizag"),
    ("CYL-SK-32", "Cylinder seal kit 32mm bore", "Pneumatics", "Blister Packer,Cartoner,Strip Packer", 2, 5, 15, 640, "Festo Distributor Vizag"),
    ("FRL-FLT-05", "FRL filter element 5 micron", "Pneumatics", "All", 7, 6, 20, 450, "Festo Distributor Vizag"),
    ("FIT-PU8", "Push-in fitting 8mm (pack of 10)", "Pneumatics", "All", 25, 10, 30, 380, "Festo Distributor Vizag"),
    ("BELT-HTD8M", "Timing belt HTD-8M 1200", "Transmission", "Blister Packer,Cartoner", 3, 3, 6, 1800, "Gates India"),
    ("CH-CT120", "Transfer chain CT-120", "Transmission", "Cartoner", 2, 1, 2, 18500, "Packtech Systems"),
    ("PD-BP250", "Punch & die set BP-250", "Tooling", "Blister Packer", 2, 2, 4, 42000, "Packtech Systems"),
    ("SP-BP250", "Sealing plate (knurled) BP-250", "Tooling", "Blister Packer", 1, 1, 2, 65000, "Packtech Systems"),
    ("CB-SP4", "Cutter blade set SP-4", "Tooling", "Strip Packer", 6, 4, 8, 5200, "Packtech Systems"),
    ("SR-BR-04", "Slip-ring brush set", "Electrical", "Strip Packer", 3, 2, 6, 1650, "Packtech Systems"),
    ("VC-20", "Vacuum suction cup 20mm", "Pneumatics", "Cartoner", 40, 20, 50, 95, "Piab India"),
    ("VP-FLT", "Vacuum pump inlet filter", "Pneumatics", "Cartoner", 3, 2, 6, 980, "Piab India"),
    ("PS-KIT-FC8", "Piston O-ring & NRV kit FC-8", "Seals", "Filler-Capper", 4, 3, 8, 3400, "Packtech Systems"),
    ("CHK-INSERT", "Capping chuck insert", "Tooling", "Filler-Capper", 8, 4, 10, 1250, "Packtech Systems"),
    ("PH-LB300", "Thermal printhead 53mm", "Electrical", "Labeller", 1, 1, 2, 28500, "Zebra Distributor"),
    ("RIB-110", "Thermal transfer ribbon 110mm", "Consumables", "Labeller", 60, 30, 100, 260, "Zebra Distributor"),
    ("SNS-PHOTO-01", "Photoelectric sensor diffuse", "Sensors", "All", 5, 3, 8, 3100, "Sick India"),
    ("LC-CW60", "Load cell CW-60", "Sensors", "Checkweigher", 1, 1, 1, 24000, "Packtech Systems"),
    ("GRS-NLGI2", "Lithium grease NLGI-2 (1kg)", "Lubricants", "All", 12, 4, 10, 520, "Servo Lubricants"),
    ("OIL-VG220", "Gear oil ISO VG 220 (5L)", "Lubricants", "All", 3, 3, 6, 2100, "Servo Lubricants"),
    ("OIL-H1", "Food-grade H1 chain oil (1L)", "Lubricants", "Cartoner", 6, 4, 10, 890, "Servo Lubricants"),
    ("CPL-JAW-42", "Jaw coupling spider 42", "Transmission", "All", 7, 3, 8, 450, "Lovejoy India"),
]


def seed(db: Session, days: int = 30):
    now = now_local()
    today = now.date()
    # ---------------------------------------------------------------- settings
    for k, (v, d) in services.DEFAULT_SETTINGS.items():
        db.add(M.Setting(key=k, value=v, description=d))
    # ---------------------------------------------------------------- users
    def user(u, name, role, pw, email=None):
        x = M.User(username=u, full_name=name, role=role, password_hash=hash_password(pw),
                   email=email or f"{u}@factoryguard.local", phone=f"+91 9{R.randint(100000000, 999999999)}")
        db.add(x)
        return x
    admin = user("admin", "System Administrator", "admin", "Admin@123")
    head = user("planthead", "Ravi Varma (Plant Head)", "plant_head", "Plant@123")
    techs = [user(f"tech{i}", n, "technician", "Tech@123") for i, n in enumerate(
        ["Srinivas Rao", "Lakshmi Prasad", "Venkatesh Naidu", "Anil Kumar", "Suresh Babu"], 1)]
    op_names = ["Ramesh K", "Sravani P", "Kiran M", "Divya S", "Prakash R", "Swathi N", "Naveen T", "Bhavani L",
                "Mahesh G", "Pavani D", "Sai Teja", "Haritha B", "Gopi Krishna", "Madhavi V", "Raju Ch"]
    ops = [user(f"op{i:02d}", n, "operator", "Oper@123") for i, n in enumerate(op_names, 1)]
    db.flush()
    # ---------------------------------------------------------------- shifts
    shifts = [M.Shift(code="A", name="Morning", start_time="06:00", end_time="14:00", supervisor_id=techs[0].id),
              M.Shift(code="B", name="Afternoon", start_time="14:00", end_time="22:00", supervisor_id=techs[1].id),
              M.Shift(code="C", name="Night", start_time="22:00", end_time="06:00", supervisor_id=techs[2].id)]
    db.add_all(shifts)
    db.flush()
    # ---------------------------------------------------------------- machines
    machines = []
    line_no = 0
    for mtype, pfx, n, speed, mfr, model, kw, tmax in MACHINE_TYPES:
        for i in range(1, n + 1):
            line_no += 1
            m = M.Machine(code=f"{pfx}-{i:02d}", name=f"{mtype} {i}", machine_type=mtype, manufacturer=mfr,
                          model=model, serial_no=f"{model}-{2016 + R.randint(0, 8)}-{R.randint(1000, 9999)}",
                          line=f"Line-{(line_no - 1) % 5 + 1}", install_date=date(2016 + R.randint(0, 8), R.randint(1, 12), 1),
                          rated_speed=speed, power_kw=kw, criticality="A" if mtype in ("Blister Packer", "Filler-Capper") else
                          ("B" if mtype in ("Cartoner", "Strip Packer") else "C"),
                          pm_interval_days=30, last_pm_date=today - timedelta(days=R.randint(3, 40)),
                          temp_max=tmax, vibration_max=7.1, pressure_min=4.5, pressure_max=7.0,
                          specs=json.dumps({"voltage": "415V 3-phase 50Hz", "air": "6 bar, 400 NL/min",
                                            "rated_output": f"{speed} units/h", "footprint_m": "4.2 x 1.4",
                                            "weight_kg": R.choice([900, 1200, 1800, 2400])}))
            machines.append(m)
    db.add_all(machines)
    db.flush()
    # ---------------------------------------------------------------- spare parts
    parts = {}
    for pn, name, cat, compat, qty, lvl, rq, cost, sup in PARTS:
        p = M.SparePart(part_no=pn, name=name, category=cat, compatible_types=compat, quantity=qty, reorder_level=lvl,
                        reorder_qty=rq, unit_cost=cost, location=f"Store-R{R.randint(1, 6)}-B{R.randint(1, 12)}",
                        supplier=sup, lead_time_days=R.choice([3, 5, 7, 10, 14, 21]))
        db.add(p)
        parts[pn] = p
    db.flush()
    # ---------------------------------------------------------------- history
    def shift_window(d, s):
        st = datetime.combine(d, time(*map(int, s.start_time.split(":"))))
        return st, st + timedelta(hours=8)

    tcount = 0
    for back in range(days, -1, -1):
        d = today - timedelta(days=back)
        for s in shifts:
            s_start, s_end = shift_window(d, s)
            if s_end > now:
                continue
            pending = []
            for idx, m in enumerate(machines):
                mult = BAD_ACTORS.get(m.code, 1.0)
                operator = ops[(idx + shifts.index(s) * 5 + back) % len(ops)]
                # downtime events in this shift
                dt_total = 0
                n_events = sum(R.random() < 0.13 * mult for _ in range(2))
                for _ in range(n_events):
                    reason = R.choices([r for r, _ in DT_REASONS], [w for _, w in DT_REASONS])[0]
                    if reason == "breakdown" and mult > 1:
                        dur = R.uniform(40, 150)
                    else:
                        dur = {"breakdown": R.uniform(20, 120), "changeover": R.uniform(20, 60),
                               "planned_maintenance": R.uniform(60, 120)}.get(reason, R.uniform(10, 45))
                    dur = min(dur, 420 - dt_total)
                    if dur < 5:
                        continue
                    st = s_start + timedelta(minutes=R.uniform(10, 470 - dur))
                    ticket = None
                    if reason == "breakdown":
                        tcount += 1
                        rc = R.choice(ROOT_CAUSES)
                        tech = R.choice(techs)
                        ticket = M.MaintenanceTicket(
                            ticket_no=f"MT-{st.year}-{tcount:05d}", machine_id=m.id,
                            title=f"{m.code}: {R.choice(DT_DETAIL['breakdown'])}", description="Reported by operator on shift",
                            category=rc[0], priority=R.choice(["high", "critical"] if m.criticality == "A" else ["medium", "high"]),
                            status="closed", ticket_type="breakdown", source="operator", reported_by_id=operator.id,
                            assigned_to_id=tech.id, created_at=st, assigned_at=st + timedelta(minutes=5),
                            started_at=st + timedelta(minutes=12), resolved_at=st + timedelta(minutes=dur),
                            closed_at=st + timedelta(minutes=dur + 30), root_cause=rc[1], action_taken=rc[2],
                            labour_hours=round(dur / 60 + 0.3, 1))
                        db.add(ticket)
                        db.flush()
                        db.add(M.PartUsage(ticket_id=ticket.id, part_id=parts[rc[3]].id, quantity=1, used_by_id=tech.id,
                                           created_at=st + timedelta(minutes=dur * 0.6)))
                        for msg, k, off in (("Ticket raised", "status", 0), (f"Assigned to {tech.full_name}", "assign", 5),
                                            ("open → in_progress", "status", 12), ("in_progress → resolved", "status", dur)):
                            db.add(M.TicketActivity(ticket_id=ticket.id, user_id=tech.id, kind=k, message=msg,
                                                    created_at=st + timedelta(minutes=off)))
                    db.add(M.DowntimeEvent(machine_id=m.id, shift_id=s.id, ticket_id=ticket.id if ticket else None,
                                           reported_by_id=operator.id, start_time=st, end_time=st + timedelta(minutes=dur),
                                           duration_min=round(dur, 1), reason_category=reason,
                                           reason_detail=R.choice(DT_DETAIL[reason]), is_planned=reason in ("planned_maintenance", "changeover")))
                    dt_total += dur
                # production log
                planned_time = 480 - 30            # 30 min breaks
                run = max(0, planned_time - dt_total)
                ideal = 3600 / m.rated_speed
                perf = min(0.98, max(0.55, R.gauss(0.86 - 0.04 * (mult - 1), 0.05)))
                produced = int(run * 60 / ideal * perf)
                rej_rate = max(0.001, R.gauss(0.012 * mult, 0.006))
                rejected = int(produced * rej_rate)
                prod_name = R.choice(PRODUCTS[m.machine_type])
                batch = f"B{d:%y%m%d}{s.code}-{m.code.replace('-', '')}"
                pl = M.ProductionLog(machine_id=m.id, shift_id=s.id, operator_id=operator.id, log_date=d,
                                     product_name=prod_name, batch_no=batch, planned_qty=int(planned_time * 60 / ideal * 0.85),
                                     produced_qty=produced, rejected_qty=rejected, planned_time_min=planned_time,
                                     run_time_min=round(run, 1), ideal_cycle_sec=round(ideal, 3),
                                     created_at=s_end - timedelta(minutes=10))
                db.add(pl)
                db.flush()
                # quality inspection for the batch (risk scored in one vectorised call per shift below)
                st_dev = 1.0 + 0.8 * (mult - 1)
                pending.append((m, pl, operator, batch, {
                    "seal_temp": round(R.gauss(150, 4 * st_dev), 1), "seal_pressure": round(R.gauss(4.5, 0.25 * st_dev), 2),
                    "line_speed": round(R.gauss(45, 5), 1), "fill_weight_dev": round(R.gauss(0, 0.8 * st_dev), 2),
                    "humidity": round(R.gauss(47, 6), 1)}))
            if pending:
                risks = defect_risk_batch([p[4] for p in pending])
                for (m, pl, operator, batch, prm), risk in zip(pending, risks):
                    sample = 200
                    defects = sum(R.random() < (0.002 + 0.03 * risk) for _ in range(sample))
                    rate = defects / sample
                    dtype = "none"
                    if defects:
                        dtype = R.choice(DEFECT_TYPES[m.machine_type])
                    db.add(M.QualityInspection(batch_no=batch, machine_id=m.id, production_log_id=pl.id, shift_id=s.id,
                                               inspector_id=operator.id, inspected_at=s_end - timedelta(minutes=5),
                                               sample_size=sample, defects_found=defects, defect_type=dtype, **prm,
                                               result="fail" if rate > 0.02 else "rework" if rate > 0.005 else "pass",
                                               ai_defect_risk=risk))
                pending.clear()
        db.flush()
    # ---------------------------------------------------------------- open / in-progress tickets now
    open_specs = [("BP-03", "Abnormal noise from indexing gearbox", "mechanical", "high", "in_progress", 1),
                  ("CT-02", "Cartons not opening, vacuum low", "pneumatic", "medium", "assigned", 2),
                  ("SP-04", "Seal leak on track 3", "mechanical", "high", "open", None),
                  ("LB-05", "OCR rejecting good labels", "sensor", "low", "open", None),
                  ("FC-02", "Head 4 underfilling", "mechanical", "medium", "on_hold", 3)]
    for code, title, cat, pri, st, ti in open_specs:
        m = next(x for x in machines if x.code == code)
        tcount += 1
        created = now - timedelta(hours=R.uniform(2, 20))
        t = M.MaintenanceTicket(ticket_no=f"MT-{now.year}-{tcount:05d}", machine_id=m.id, title=f"{code}: {title}",
                                description="Reported during shift round", category=cat, priority=pri, status=st,
                                reported_by_id=R.choice(ops).id, assigned_to_id=techs[ti].id if ti else None,
                                created_at=created, assigned_at=created + timedelta(minutes=20) if ti else None,
                                started_at=created + timedelta(minutes=40) if st in ("in_progress", "on_hold") else None)
        db.add(t)
        db.flush()
        db.add(M.TicketActivity(ticket_id=t.id, user_id=t.reported_by_id, kind="status", message="Ticket raised", created_at=created))
    # one ongoing downtime
    bp3 = next(x for x in machines if x.code == "BP-03")
    db.add(M.DowntimeEvent(machine_id=bp3.id, start_time=now - timedelta(minutes=55), reason_category="breakdown",
                           reason_detail="Indexing gearbox noise — stopped for inspection", reported_by_id=ops[0].id))
    bp3.status = "maintenance"
    next(x for x in machines if x.code == "CW-05").status = "idle"
    # ---------------------------------------------------------------- roster (last 7 days + next 2)
    for back in range(-2, 8):
        d = today - timedelta(days=back)
        for i, op in enumerate(ops):
            db.add(M.ShiftAssignment(user_id=op.id, shift_id=shifts[(i + back) % 3].id, machine_id=machines[(i * 2 + back) % 40].id, work_date=d))
        for i, t in enumerate(techs):
            db.add(M.ShiftAssignment(user_id=t.id, shift_id=shifts[(i + back) % 3].id, work_date=d))
    db.flush()
    # ---------------------------------------------------------------- sensor history (last 36h, every 30 min)
    from .ml.inference import detect_anomaly_batch
    all_shifts = shifts
    for idx, m in enumerate(machines):
        mult = BAD_ACTORS.get(m.code, 1.0)
        base_t, base_v = R.uniform(50, 57), R.uniform(2.0, 3.2)
        wear = R.uniform(0, 100)
        rows = []
        for k in range(72, 0, -1):
            ts = now - timedelta(minutes=30 * k)
            drift = (72 - k) / 72 * (mult - 1)        # bad actors degrade over the window
            data = {"temperature": round(R.gauss(base_t + drift * 6, 1.5), 2),
                    "vibration": round(max(0.4, R.gauss(base_v + drift * 3.2, 0.35)), 2),
                    "pressure": round(R.gauss(6.0 - drift * 0.6, 0.12), 2),
                    "rpm": round(R.gauss(1500, 35), 1), "torque": round(max(5, R.gauss(40 + drift * 8, 4)), 2),
                    "tool_wear": round(wear + (72 - k) * 1.5, 1), "humidity": round(R.gauss(46, 3), 1),
                    "recorded_at": ts}
            data["power_kw"] = round(data["torque"] * data["rpm"] * 6.2832 / 60000, 2)
            rows.append(data)
        results = detect_anomaly_batch(rows, [services.limits_of(m)] * len(rows))
        for k, (data, res) in enumerate(zip(rows, results)):
            services.ingest_reading(db, m, data, source="iot", run_prediction=(k == len(rows) - 1),
                                    anomaly=res, shifts=all_shifts)
    # low-stock alerts
    for p in parts.values():
        services.check_reorder(db, p)
    db.commit()
    print(f"Seeded: {len(machines)} machines, {len(ops) + len(techs) + 2} users, {tcount} tickets.")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    seed(s)
    s.close()
