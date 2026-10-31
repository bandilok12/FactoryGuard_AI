"""FactoryGuard AI — FastAPI application entry point."""
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

from . import config, models as M
from .database import Base, engine, SessionLocal
from .routers import auth_admin, machines, operations, maintenance, quality, dashboard, ai, sim

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("factoryguard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if not db.scalar(select(func.count(M.User.id))):
            log.info("Empty database — loading synthetic seed data …")
            from .seed import seed
            seed(db)
    finally:
        db.close()
    from .ml import inference
    from .genai import rag
    inference._load()
    rag.build_index()
    from .simulator import simulator
    if config.SIMULATOR_AUTOSTART:
        db = SessionLocal()
        ids = [m.id for m in db.scalars(select(M.Machine))]
        db.close()
        simulator.seed_degradation(ids, n=3)
        simulator.start()
        log.info("Sensor simulator started (every %.0fs)", simulator.interval)
    yield
    simulator.stop()


DESCRIPTION = """
**FactoryGuard AI** — predictive maintenance & quality platform for a 40-machine pharmaceutical packaging unit.

* **Auth**: click *Authorize* and log in (e.g. `admin` / `Admin@123`). JWT bearer tokens, 4 roles.
* **AI (scikit-learn)**: failure prediction (RandomForest), hybrid anomaly detection (IsolationForest + rules),
  pre-batch defect-risk (GradientBoosting).
* **GenAI (Gemini / Groq / OpenAI, with offline fallback)**: troubleshooting copilot (RAG over manuals),
  shift report generator, smart ticket triage, quality RCA & CAPA.
* **Bonus**: real-time sensor simulator, spare-parts reorder alerts, OEE analytics.
"""

app = FastAPI(title=config.APP_NAME, version=config.VERSION, description=DESCRIPTION, lifespan=lifespan,
              docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])

# --- simple in-memory rate limiter for login endpoints (brute-force protection) ---
_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def security_mw(request: Request, call_next):
    if request.url.path in ("/api/auth/login", "/api/auth/token") and request.method == "POST":
        ip = request.client.host if request.client else "?"
        q, now = _hits[ip], time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= 20:
            return JSONResponse({"detail": "Too many login attempts. Wait a minute."}, status_code=429)
        q.append(now)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e["loc"] if x not in ("body", "query", "path"))
        errors.append(f"{loc}: {e['msg']}" if loc else e["msg"])
    return JSONResponse({"detail": "; ".join(errors)}, status_code=422)


for r in (auth_admin.router, machines.router, operations.router, maintenance.router, quality.router,
          dashboard.router, ai.router, sim.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health", tags=["System"])
def health():
    from .genai.llm import active_provider
    return {"status": "ok", "app": config.APP_NAME, "version": config.VERSION, "llm_provider": active_provider(),
            "database": "postgresql" if "postgres" in config.DATABASE_URL else
                        "mysql" if "mysql" in config.DATABASE_URL else "sqlite"}


# --- serve the frontend SPA ---
if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(config.FRONTEND_DIR / "index.html")
