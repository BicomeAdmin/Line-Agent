from __future__ import annotations

try:
    from fastapi import FastAPI, Request
except ImportError:  # pragma: no cover - lets scripts/tests run without FastAPI installed.
    FastAPI = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]

from app.core.jobs import job_registry
from app.lark.events import enqueue_lark_action, enqueue_lark_event
from app.workflows.action_queue import get_action_queue
from app.workflows.audit_status import get_audit_status
from app.workflows.acceptance_status import get_acceptance_status
from app.workflows.calibration_status import get_calibration_status
from app.workflows.community_status import get_community_status
from app.workflows.dashboard_status import get_dashboard_status
from app.workflows.device_status import get_device_status
from app.workflows.device_recovery import ensure_device_ready
from app.workflows.job_runner import ensure_job_worker
from app.workflows.line_apk_status import get_line_apk_status
from app.workflows.milestone_status import get_milestone_status
from app.workflows.onboarding_timeline import get_onboarding_timeline
from app.workflows.openchat_validation import validate_openchat_session
from app.workflows.project_snapshot import get_project_snapshot
from app.workflows.readiness_status import get_readiness_status
from app.workflows.review_status import get_review_status
from app.workflows.scheduler import enqueue_due_patrols
from app.workflows.scheduler_runner import ensure_scheduler_runner
from app.workflows.system_status import get_system_status


if FastAPI is not None:
    app = FastAPI(title="Project Echo")
    ensure_job_worker()
    ensure_scheduler_runner()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status/system")
    async def system_status() -> dict[str, object]:
        return get_system_status()

    @app.get("/status/dashboard")
    async def dashboard_status(audit_limit_per_customer: int = 20) -> dict[str, object]:
        return get_dashboard_status(audit_limit_per_customer=audit_limit_per_customer)

    @app.get("/status/reviews")
    async def review_status() -> dict[str, object]:
        return get_review_status()

    @app.get("/status/calibration")
    async def calibration_status() -> dict[str, object]:
        return get_calibration_status()

    @app.get("/status/communities")
    async def communities_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return get_community_status(customer_id=customer_id, community_id=community_id)

    @app.get("/status/acceptance")
    async def acceptance_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return get_acceptance_status(customer_id=customer_id, community_id=community_id)

    @app.get("/status/onboarding")
    async def onboarding_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return get_onboarding_timeline(customer_id=customer_id, community_id=community_id)

    @app.get("/status/openchat")
    async def openchat_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return validate_openchat_session(customer_id=customer_id, community_id=community_id)

    @app.get("/status/line-apk")
    async def line_apk_status() -> dict[str, object]:
        return get_line_apk_status()

    @app.get("/status/project-snapshot")
    async def project_snapshot(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return get_project_snapshot(customer_id=customer_id, community_id=community_id)

    @app.get("/status/action-queue")
    async def action_queue(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return get_action_queue(customer_id=customer_id, community_id=community_id)

    @app.get("/status/milestones")
    async def milestone_status(customer_id: str | None = None, community_id: str | None = None) -> dict[str, object]:
        return get_milestone_status(customer_id=customer_id, community_id=community_id)

    @app.get("/status/readiness")
    async def readiness_status() -> dict[str, object]:
        return get_readiness_status()

    @app.get("/status/device/{device_id}")
    async def device_status(device_id: str) -> dict[str, object]:
        return get_device_status(device_id)

    @app.post("/devices/{device_id}/ensure-ready")
    async def ensure_ready(device_id: str, wait_timeout_seconds: int = 60) -> dict[str, object]:
        return ensure_device_ready(device_id, wait_timeout_seconds=wait_timeout_seconds)

    @app.get("/status/audit/{customer_id}")
    async def audit_status(customer_id: str, limit: int = 20) -> dict[str, object]:
        return get_audit_status(customer_id, limit=limit)

    @app.get("/status/jobs")
    async def jobs() -> dict[str, object]:
        return {
            "jobs": [
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "error": job.error,
                }
                for job in job_registry.list_jobs()
            ]
        }

    @app.post("/scheduler/tick")
    async def scheduler_tick() -> dict[str, object]:
        return enqueue_due_patrols()

    @app.post("/webhooks/lark/events")
    async def lark_events(request: Request) -> dict[str, object]:
        payload = await request.json()
        return enqueue_lark_event(payload)

    @app.post("/webhooks/lark/actions")
    async def lark_actions(request: Request) -> dict[str, object]:
        payload = await request.json()
        return enqueue_lark_action(payload)
else:
    app = None
