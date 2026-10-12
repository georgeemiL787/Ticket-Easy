"""JSON API: /health and the /api/v1 routers, grouped by domain."""
from fastapi import APIRouter
from . import artifacts, businesses, proposals, requests, runs

router=APIRouter()


@router.get("/health")
def health():
    return {"status":"ok","scope":"approved_to_build_only"}


router.include_router(businesses.router)
router.include_router(proposals.router)
router.include_router(artifacts.router)
router.include_router(requests.router)
router.include_router(runs.router)
