"""RepoSentry scanner worker – receives Pub/Sub push messages and executes scans."""

import base64
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request, status

from app.config import settings
from app.services.scan_runner import execute_scan

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="RepoSentry Scanner Worker",
    version="0.1.0",
    description="Receives Pub/Sub push messages and executes security scans",
)

# Thread pool for concurrent scan execution
_executor = ThreadPoolExecutor(max_workers=4)

# Track in-flight scans to reject duplicates
_in_flight: set[uuid.UUID] = set()


def _submit_scan(scan_id: uuid.UUID) -> dict[str, str]:
    """Deduplicate and dispatch a scan to the background executor."""
    if scan_id in _in_flight:
        logger.info("Scan %s already in flight, acknowledging duplicate", scan_id)
        return {"status": "duplicate", "scan_id": str(scan_id)}

    _in_flight.add(scan_id)
    logger.info("Dispatching scan %s to worker thread", scan_id)

    def _run_and_cleanup() -> None:
        try:
            execute_scan(scan_id)
        finally:
            _in_flight.discard(scan_id)

    _executor.submit(_run_and_cleanup)
    return {"status": "accepted", "scan_id": str(scan_id)}


@app.get("/")
async def root():
    return {"service": "reposentry-worker", "status": "ok"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/scan")
async def receive_scan_job(request: Request):
    """Pub/Sub push endpoint.

    Google Pub/Sub sends a POST with this structure:
    {
        "message": {
            "data": "<base64-encoded JSON>",
            "messageId": "...",
            "publishTime": "..."
        },
        "subscription": "projects/.../subscriptions/..."
    }

    The base64-decoded data contains: {"scan_id": "<uuid>"}

    Returns 200/204 to acknowledge the message (prevents redelivery).
    Returns 400 for malformed messages.
    Returns 409 if the scan is already in flight.
    """
    # ── Parse the Pub/Sub envelope ───────────────────────────────
    try:
        envelope = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    message = envelope.get("message")
    if not message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'message' field")

    # Decode the data payload
    raw_data = message.get("data", "")
    try:
        decoded = base64.b64decode(raw_data)
        payload = json.loads(decoded)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message data")

    scan_id_str = payload.get("scan_id")
    if not scan_id_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'scan_id' in message data")

    try:
        scan_id = uuid.UUID(scan_id_str)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid scan_id format")

    return _submit_scan(scan_id)


@app.post("/scan/direct")
async def receive_scan_direct(request: Request):
    """Direct scan trigger for local development (no Pub/Sub envelope).

    Accepts: {"scan_id": "<uuid>"}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    scan_id_str = body.get("scan_id")
    if not scan_id_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'scan_id'")

    try:
        scan_id = uuid.UUID(scan_id_str)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid scan_id format")

    return _submit_scan(scan_id)


@app.on_event("shutdown")
def shutdown_executor():
    """Gracefully shut down the thread pool."""
    logger.info("Shutting down worker thread pool")
    _executor.shutdown(wait=True, cancel_futures=False)
