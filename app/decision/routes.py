"""Mount under the gateway's existing authenticated middleware."""
from fastapi import APIRouter

from app.config import get_settings
from app.decision.service import DecisionRequest, DecisionResponse, decide
from app.providers.registry import get_registry

router = APIRouter()


@router.post("/v1/spark-decide", response_model=DecisionResponse, tags=["decision"])
async def spark_decide(request: DecisionRequest) -> DecisionResponse:
    return await decide(request, get_registry(), get_settings().qwen_model)
