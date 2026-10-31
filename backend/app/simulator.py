"""
Real-time sensor simulator (bonus feature).

A background thread produces one reading per running machine every SIMULATOR_INTERVAL_SEC.
Each machine carries a hidden degradation state; a few machines slowly degrade (bearing wear,
heater drift, air leak, tool wear) so the anomaly detector and failure-prediction model have
something real to catch. Resolving a maintenance ticket for the machine "repairs" it.
Admins can inject a fault from the UI for live demos.
"""
import random
import threading
import time
import logging

from sqlalchemy import select

from . import config, models as M, services
from .database import SessionLocal
from .utils import now_local
from .ml import inference

log = logging.getLogger("simulator")
MODES = ["bearing", "overheat", "pneumatic", "tool_wear"]


class Simulator:
    def __init__(self):
        self.state: dict[int, dict] = {}
        self.thread = None
        self.running = False
        self.interval = config.SIMULATOR_INTERVAL_SEC
        self.ticks = 0
        self.last_tick = None
        self.readings = 0
        self.lock = threading.Lock()

    # ----------------------------------------------------------------- control
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="sensor-simulator")
        self.thread.start()

    def stop(self):
        self.running = False

    def status(self):
        with self.lock:
            degrading = [{"machine_id": k, "mode": v["mode"], "severity": round(v["sev"], 2)}
                         for k, v in self.state.items() if v["mode"]]
        return {"running": self.running, "interval_sec": self.interval, "ticks": self.ticks,
                "readings_generated": self.readings, "last_tick": self.last_tick.isoformat() if self.last_tick else None,
                "degrading_machines": degrading}

    def inject(self, machine_id: int, mode: str, severity: float = 0.75):
        with self.lock:
            st = self.state.setdefault(machine_id, self._new_state())
            st["mode"], st["sev"] = mode, severity
            if mode == "tool_wear":
                st["wear"] = 205

    # ----------------------------------------------------------------- internals
    @staticmethod
    def _new_state():
        return {"mode": None, "sev": 0.0, "wear": random.uniform(0, 120), "repaired_at": now_local(),
                "temp_base": random.uniform(50, 58), "vib_base": random.uniform(2.0, 3.4)}

    def seed_degradation(self, machine_ids, n=3):
        for mid in random.sample(list(machine_ids), min(n, len(machine_ids))):
            st = self.state.setdefault(mid, self._new_state())
            st["mode"], st["sev"] = random.choice(MODES), random.uniform(0.25, 0.45)

    def _reading(self, st):
        sev, mode = st["sev"], st["mode"]
        r = {
            "temperature": random.gauss(st["temp_base"], 1.6),
            "vibration": max(0.4, random.gauss(st["vib_base"], 0.35)),
            "pressure": random.gauss(6.0, 0.12),
            "rpm": random.gauss(1500, 35),
            "torque": max(5, random.gauss(40, 4)),
            "tool_wear": st["wear"],
            "humidity": random.gauss(46, 3),
        }
        if mode == "bearing":
            r["vibration"] += sev * 8.5
            r["temperature"] += sev * 7
        elif mode == "overheat":
            r["temperature"] += sev * 32
            r["rpm"] -= sev * 150
        elif mode == "pneumatic":
            r["pressure"] -= sev * 2.4
        elif mode == "tool_wear":
            r["torque"] += sev * 30
        if random.random() < 0.0015:  # occasional transient spike
            k = random.choice(["temperature", "vibration", "pressure"])
            r[k] = r[k] * (1.5 if k != "pressure" else 0.65)
        r["power_kw"] = round(r["torque"] * r["rpm"] * 6.2832 / 60 / 1000, 2)
        return {k: round(v, 2) for k, v in r.items()}

    def _tick(self):
        db = SessionLocal()
        try:
            machines = db.scalars(select(M.Machine)).all()
            # repairs: a resolved ticket after the degradation began resets the machine
            resolved = dict(db.execute(select(M.MaintenanceTicket.machine_id, M.MaintenanceTicket.resolved_at)
                                       .where(M.MaintenanceTicket.resolved_at.isnot(None))).all())
            with self.lock:
                for m in machines:
                    st = self.state.setdefault(m.id, self._new_state())
                    if not st.get("init"):
                        st["init"] = True
                        last = services.recent_readings(db, m.id, 5)
                        if last:   # continue from the machine's real baseline (no step change)
                            st["temp_base"] = min(60, sum(r.temperature for r in last) / len(last))
                            st["vib_base"] = min(3.6, sum(r.vibration for r in last) / len(last))
                            st["wear"] = last[-1].tool_wear % 200
                    if resolved.get(m.id) and resolved[m.id] > st["repaired_at"]:
                        st.update(mode=None, sev=0.0, wear=0.0, repaired_at=resolved[m.id])
                    if m.status != "running":
                        continue
                    st["wear"] += self.interval / 60 * 2      # accelerated tool wear
                    if st["wear"] > 240:
                        st["wear"] = 0.0                      # tool changed by operator
                    if st["mode"]:
                        st["sev"] = min(1.0, st["sev"] + random.uniform(0.002, 0.008))
                    elif random.random() < 0.00005:   # ~1 new degradation per 1.5 h across the fleet
                        st["mode"], st["sev"] = random.choice(MODES), 0.05
            predict_now = self.ticks % 3 == 0
            live = [m for m in machines if m.status == "running"]
            datas = [self._reading(self.state[m.id]) for m in live]
            results = inference.detect_anomaly_batch(datas, [services.limits_of(m) for m in live])
            shifts = db.scalars(select(M.Shift)).all()
            for m, data, res in zip(live, datas, results):
                services.ingest_reading(db, m, data, source="simulator", run_prediction=predict_now,
                                        anomaly=res, shifts=shifts)
                self.readings += 1
            db.commit()
        except Exception as e:   # never kill the thread
            db.rollback()
            log.exception("simulator tick failed: %s", e)
        finally:
            db.close()
        self.ticks += 1
        self.last_tick = now_local()

    def _loop(self):
        while self.running:
            t0 = time.time()
            self._tick()
            time.sleep(max(0.5, self.interval - (time.time() - t0)))


simulator = Simulator()
