from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import comparison, findings, health, repositories, scans

app = FastAPI(
    title="RepoSentry API",
    version="0.1.0",
    description="LLM-powered Python security scanner platform",
)

cors_allowed_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_origin_regex=settings.cors_allowed_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(repositories.router)
app.include_router(scans.router)
app.include_router(findings.router)
app.include_router(comparison.router)


@app.get("/")
async def root():
    return {"service": "reposentry-api", "status": "ok"}
