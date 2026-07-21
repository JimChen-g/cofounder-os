"""Seed a governed, synthetic traffic-liability case for product demos.

The fixture deliberately does not call a model.  It presents a repeatable
Qwen-derived demonstration result while the real training data and adapter are
still being prepared.  The generated Run uses only the accepted D06-D13 state,
artifact, approval, audit, and evaluation contracts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from app.artifacts import FileArtifactStore
from app.domain import ApprovalStatus, RunStatus, TaskStatus, utc_now
from app.evaluation.service import REQUIRED_ARTIFACTS
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService
from app.state import FileStateRepository


DEMO_ACTOR = "traffic-liability-demo-seeder"
DEMO_MODEL = "qwen-traffic-liability-demo-fixture-v0"
DEMO_PROVIDER = "qwen"
DEMO_INFERENCE_MODE = "deterministic_demo_fixture"
DEMO_ADAPTER_STATUS = "pending_clean_dataset_and_formal_adapter"


class TrafficLiabilityDemoError(ValueError):
    """Raised when a demo fixture is unsafe or incomplete."""


@dataclass(frozen=True)
class TrafficLiabilityDemoResult:
    """Identifiers and paths needed to launch or inspect the seeded demo."""

    run_id: UUID
    approval_id: UUID
    data_dir: Path
    case_id: str
    created: bool


def _required_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrafficLiabilityDemoError(f"{field} must be an object")
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrafficLiabilityDemoError(f"{field} must be non-empty text")
    return value.strip()


def _load_case(case_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrafficLiabilityDemoError(
            f"Could not read traffic demo fixture: {case_path}"
        ) from exc

    case = _required_mapping(value, "case")
    _required_text(case.get("case_id"), "case.case_id")
    _required_text(case.get("title"), "case.title")
    _required_text(case.get("disclaimer"), "case.disclaimer")
    if case.get("synthetic") is not True:
        raise TrafficLiabilityDemoError(
            "The demo fixture must be explicitly marked synthetic=true"
        )

    model = _required_mapping(case.get("model"), "case.model")
    if model.get("inference_mode") != DEMO_INFERENCE_MODE:
        raise TrafficLiabilityDemoError(
            "The unfinished model must use deterministic_demo_fixture mode"
        )
    if model.get("adapter_status") != DEMO_ADAPTER_STATUS:
        raise TrafficLiabilityDemoError(
            "The fixture must disclose that the formal adapter is pending"
        )
    confidence = model.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise TrafficLiabilityDemoError(
            "case.model.confidence must be between 0 and 1"
        )

    scenario = _required_mapping(case.get("scenario"), "case.scenario")
    vehicles = scenario.get("vehicles")
    if not isinstance(vehicles, list) or len(vehicles) < 2:
        raise TrafficLiabilityDemoError(
            "case.scenario.vehicles must contain at least two vehicles"
        )

    prediction = _required_mapping(case.get("prediction"), "case.prediction")
    allocations = prediction.get("allocations")
    if not isinstance(allocations, list) or len(allocations) < 2:
        raise TrafficLiabilityDemoError(
            "case.prediction.allocations must contain at least two entries"
        )
    shares: list[float] = []
    for item in allocations:
        if not isinstance(item, dict):
            break
        value = item.get("share_percent")
        if not isinstance(value, (int, float)):
            break
        shares.append(float(value))
    if len(shares) != len(allocations) or abs(sum(shares) - 100) > 0.001:
        raise TrafficLiabilityDemoError(
            "Liability allocation percentages must be numeric and total 100"
        )

    review = _required_mapping(case.get("human_review"), "case.human_review")
    if review.get("required") is not True:
        raise TrafficLiabilityDemoError(
            "Human review must remain required for this demo"
        )
    return case


def _task_metadata(
    case: dict[str, Any],
    *,
    task_type: str,
    policy_action: str,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "demo_case_id": case["case_id"],
        "synthetic": True,
        "inference_mode": DEMO_INFERENCE_MODE,
        "adapter_status": DEMO_ADAPTER_STATUS,
        "policy_action": {
            "action": policy_action,
            "authority": "analysis_only",
            "external_write": False,
            "production_change": False,
        },
    }


def _record_attempt_and_route(
    orchestration: OrchestrationService,
    run_id: UUID,
    task_id: UUID,
    *,
    case_id: str,
    route_task_id: UUID | None,
    role: str,
) -> None:
    task = orchestration.repository.get_task(run_id, task_id)
    attempted = task.model_copy(deep=True)
    attempted.attempt_count = 1
    orchestration.repository.save_task(attempted)
    orchestration.record_route_decision(
        run_id,
        task_id=route_task_id,
        requested_model="qwen-traffic-liability-adapter",
        selected_model=DEMO_MODEL,
        provider=DEMO_PROVIDER,
        candidate_models=[DEMO_MODEL],
        fallback_used=False,
        latency_ms=0,
        reason=(
            f"Deterministic synthetic {role} result for video rehearsal; "
            "no live model inference was performed."
        ),
        actor=DEMO_ACTOR,
        metadata={
            "demo_case_id": case_id,
            "inference_mode": DEMO_INFERENCE_MODE,
            "adapter_status": DEMO_ADAPTER_STATUS,
        },
    )


def _provenance(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": "synthetic_deidentified_demo_fixture",
        "case_id": case["case_id"],
        "model_family": case["model"]["family"],
        "inference_mode": DEMO_INFERENCE_MODE,
        "adapter_status": DEMO_ADAPTER_STATUS,
        "authoritative": False,
    }


def _prediction_rows(case: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"- **车辆 {allocation['vehicle_id']}："
            f"{allocation['liability_level']}，{allocation['share_percent']}%** — "
            f"{allocation['rationale']}"
        )
        for allocation in case["prediction"]["allocations"]
    )


def _evidence_rows(case: dict[str, Any]) -> str:
    return "\n".join(
        f"- `{item['id']}` {item['type']}：{item['finding']}"
        for item in case["evidence"]
    )


def _executive_memo(case: dict[str, Any]) -> str:
    missing = "\n".join(f"- {item}" for item in case["missing_evidence"])
    reasoning = "\n".join(f"{index}. {item}" for index, item in enumerate(case["reasoning"], 1))
    return f"""# 交通事故责任辅助研判（合成演示）

> **演示边界：{case['disclaimer']}**

## 预测结论

{_prediction_rows(case)}

- **模型置信度：{case['model']['confidence']:.0%}**
- **推理标识：{DEMO_MODEL}**
- **运行方式：Qwen 衍生结果的确定性演示夹具；本次未调用实时模型**
- **Adapter 状态：等待清洗数据和正式适配器完成**

## 事故摘要

{case['scenario']['summary']}

## 证据链

{_evidence_rows(case)}

## 研判逻辑

{reasoning}

## 仍需补充的证据

{missing}

## 人工复核闸门

结果只能作为事故研判辅助。Founder/事故处理专家必须检查原始材料、证据完整性和适用规则后，才能批准生成最终演示结论；不得直接用于交警执法、司法裁判或保险定损。
"""


def _product_markdown(case: dict[str, Any]) -> str:
    timeline = "\n".join(
        f"- **{item['time']}**：{item['event']}"
        for item in case["scenario"]["timeline"]
    )
    return f"""# 事故证据解析与碰撞情景重建

> {case['disclaimer']}

## 情景

{case['scenario']['summary']}

## 时间线

{timeline}

## 已纳入证据

{_evidence_rows(case)}

## 输出边界

本产物只建立可追溯的演示证据链，不替代事故认定机关对原始证据的审查。
"""


def _finance_markdown(case: dict[str, Any]) -> str:
    exposure = case["loss_exposure"]
    rows = "\n".join(
        f"- {item['name']}：人民币 {item['estimated_cny']:,} 元（{item['basis']}）"
        for item in exposure["items"]
    )
    return f"""# 损失暴露与理赔影响评估（合成演示）

> {case['disclaimer']}

## 估算范围

{rows}

- 估算合计：人民币 {exposure['total_estimated_cny']:,} 元
- 人员伤情：{exposure['bodily_injury_status']}

## 风险提示

金额仅用于展示多 Agent 的影响评估能力。正式理赔必须以查勘、维修清单、医疗材料和保险合同为准。
"""


def _write_foundation_outputs(
    registration: ArtifactRegistrationService,
    case: dict[str, Any],
    run_id: UUID,
    *,
    product_task_id: UUID,
    finance_task_id: UUID,
) -> None:
    provenance = _provenance(case)
    correlation_id = f"demo:{case['case_id']}"

    registration.write_json(
        run_id,
        "product-brief",
        "product-brief.json",
        {
            "schema_version": "traffic-demo-1.0",
            "case_id": case["case_id"],
            "synthetic": True,
            "scenario": case["scenario"],
            "evidence": case["evidence"],
            "missing_evidence": case["missing_evidence"],
            "disclaimer": case["disclaimer"],
        },
        DEMO_ACTOR,
        task_id=product_task_id,
        relation="output",
        correlation_id=correlation_id,
        provenance=provenance,
    )
    registration.write_text(
        run_id,
        "product-brief-md",
        "product-brief.md",
        _product_markdown(case),
        DEMO_ACTOR,
        task_id=product_task_id,
        relation="output",
        correlation_id=correlation_id,
        provenance=provenance,
    )
    registration.write_json(
        run_id,
        "finance-brief",
        "finance-brief.json",
        {
            "schema_version": "traffic-demo-1.0",
            "case_id": case["case_id"],
            "synthetic": True,
            "loss_exposure": case["loss_exposure"],
            "liability_allocation": case["prediction"]["allocations"],
            "disclaimer": case["disclaimer"],
        },
        DEMO_ACTOR,
        task_id=finance_task_id,
        relation="output",
        correlation_id=correlation_id,
        provenance=provenance,
    )
    registration.write_text(
        run_id,
        "finance-brief-md",
        "finance-brief.md",
        _finance_markdown(case),
        DEMO_ACTOR,
        task_id=finance_task_id,
        relation="output",
        correlation_id=correlation_id,
        provenance=provenance,
    )


def _write_executive_outputs(
    registration: ArtifactRegistrationService,
    case: dict[str, Any],
    run_id: UUID,
    *,
    synthesis_task_id: UUID,
) -> None:
    provenance = _provenance(case)
    correlation_id = f"demo:{case['case_id']}"
    executive_outputs = {
        "executive-decision-memo": _executive_memo(case),
        "prd-product-brief": f"""# 交通事故判责预测 Demo 产品说明

- Case：{case['case_id']}
- 模型家族：{case['model']['family']}
- 当前运行：{DEMO_INFERENCE_MODE}
- 正式 Adapter：{DEMO_ADAPTER_STATUS}
- 输入：脱敏事故描述、车辆状态、时序证据、损失信息
- 输出：责任等级、比例、置信度、证据链、缺失证据和人工复核状态
- 禁止用途：直接执法、司法裁判、自动理赔或替代事故处理专家
""",
        "budget-summary": _finance_markdown(case),
        "risk-register": f"""# 风险登记册

| 风险 | 级别 | 控制措施 |
| --- | --- | --- |
| 数据仍在清洗，正式 Adapter 尚未就绪 | 高 | 演示只使用确定性合成夹具并显式标注 |
| 事故材料不完整导致责任比例偏差 | 高 | 展示缺失证据，强制人工复核 |
| 观众误解为执法或法律结论 | 高 | 页面、产物和审批中心重复显示非权威边界 |
| 合成损失金额被当作真实定损 | 中 | 标注估算来源，禁止自动理赔 |

**总原则：{case['disclaimer']}**
""",
        "action-plan": """# Demo 视频操作清单

1. 在 Mission 页面说明这是合成事故和非执法结论。
2. 展示三个 Agent 的完成状态、Qwen 路由标签和九个校验产物。
3. 打开责任研判报告，讲解 80% / 20%、86% 置信度和证据链。
4. 打开 Approval center，说明高风险结论必须由 Founder/专家复核。
5. 填写复核理由并批准，展示 Workflow Controller 收口为 Completed。
6. 打开 Audit trail 与 Evaluation，展示可追溯记录和质量评分。
""",
    }
    for logical_name, text in executive_outputs.items():
        registration.write_text(
            run_id,
            logical_name,
            f"{logical_name}.md",
            text,
            DEMO_ACTOR,
            task_id=synthesis_task_id,
            relation="output",
            correlation_id=correlation_id,
            provenance=provenance,
        )


def _existing_result(
    repository: FileStateRepository,
    data_dir: Path,
    case_id: str,
) -> TrafficLiabilityDemoResult | None:
    matching = [
        run
        for run in repository.list_runs()
        if run.metadata.get("demo_case_id") == case_id
    ]
    if not matching:
        return None
    run = max(matching, key=lambda candidate: candidate.updated_at)
    snapshot = OrchestrationService(repository).get_snapshot(run.id)
    approvals = [
        approval
        for approval in snapshot.approvals
        if approval.metadata.get("demo_case_id") == case_id
    ]
    names = {artifact.name for artifact in snapshot.artifacts}
    if not approvals or not REQUIRED_ARTIFACTS.issubset(names):
        raise TrafficLiabilityDemoError(
            "An incomplete traffic demo already exists; use force_new=True"
        )
    return TrafficLiabilityDemoResult(
        run_id=run.id,
        approval_id=max(approvals, key=lambda item: item.created_at).id,
        data_dir=data_dir,
        case_id=case_id,
        created=False,
    )


def seed_traffic_liability_demo(
    data_dir: str | Path,
    case_path: str | Path,
    *,
    force_new: bool = False,
) -> TrafficLiabilityDemoResult:
    """Create one video-ready, governed traffic-liability Run."""

    root = Path(data_dir).expanduser().resolve()
    case = _load_case(Path(case_path).expanduser().resolve())
    repository = FileStateRepository(root / "runs")
    if not force_new:
        existing = _existing_result(repository, root, case["case_id"])
        if existing is not None:
            return existing

    orchestration = OrchestrationService(repository)
    registration = ArtifactRegistrationService(
        FileArtifactStore(root),
        orchestration,
    )
    correlation_id = f"demo:{case['case_id']}"
    run, _ = orchestration.create_run(
        objective=(
            "【合成演示 · 非执法结论】交通事故责任辅助研判："
            f"{case['title']}"
        ),
        owner="Founder",
        actor="founder",
        correlation_id=correlation_id,
        metadata={
            "demo_case_id": case["case_id"],
            "demo_domain": "traffic_liability_prediction",
            "synthetic": True,
            "authoritative": False,
            "model_family": case["model"]["family"],
            "inference_mode": DEMO_INFERENCE_MODE,
            "adapter_status": DEMO_ADAPTER_STATUS,
            "disclaimer": case["disclaimer"],
        },
    )
    orchestration.start_run(
        run.id,
        actor="workflow-controller",
        reason="Start governed synthetic traffic-liability demonstration.",
        correlation_id=correlation_id,
    )

    product_task, _ = orchestration.create_task(
        run.id,
        title="事故证据解析与碰撞情景重建",
        description=(
            "解析脱敏的行车记录、信号相位、车速与碰撞位置，建立可追溯的事故时间线和证据矩阵。"
        ),
        assigned_agent="product-agent",
        actor="executive-orchestrator",
        correlation_id=correlation_id,
        metadata=_task_metadata(
            case,
            task_type="product_analysis",
            policy_action="analyze_synthetic_accident_evidence",
        ),
    )
    finance_task, _ = orchestration.create_task(
        run.id,
        title="损失暴露与理赔影响评估",
        description=(
            "基于合成维修与道路设施损失，评估责任比例变化对理赔暴露的影响；不执行自动定损。"
        ),
        assigned_agent="finance-agent",
        actor="executive-orchestrator",
        correlation_id=correlation_id,
        metadata=_task_metadata(
            case,
            task_type="finance_analysis",
            policy_action="estimate_synthetic_claim_exposure",
        ),
    )
    synthesis_task, _ = orchestration.create_task(
        run.id,
        title="责任比例综合研判与人工复核",
        description=(
            "综合事故证据和损失暴露，形成责任比例、置信度、缺失证据与人工复核要求。"
        ),
        assigned_agent="executive-orchestrator",
        dependency_ids=[product_task.id, finance_task.id],
        actor="executive-orchestrator",
        correlation_id=correlation_id,
        metadata=_task_metadata(
            case,
            task_type="artifact_synthesis",
            policy_action="synthesize_non_authoritative_liability_prediction",
        ),
    )

    for task, role in (
        (product_task, "evidence analysis"),
        (finance_task, "claim exposure"),
    ):
        orchestration.mark_task_ready(
            run.id,
            task.id,
            actor="workflow-controller",
            reason="Synthetic demo inputs are ready.",
            correlation_id=correlation_id,
        )
        orchestration.start_task(
            run.id,
            task.id,
            actor=task.assigned_agent or DEMO_ACTOR,
            reason="Execute deterministic demonstration fixture.",
            correlation_id=correlation_id,
        )
        _record_attempt_and_route(
            orchestration,
            run.id,
            task.id,
            case_id=case["case_id"],
            route_task_id=task.id,
            role=role,
        )

    _write_foundation_outputs(
        registration,
        case,
        run.id,
        product_task_id=product_task.id,
        finance_task_id=finance_task.id,
    )

    for task in (product_task, finance_task):
        orchestration.complete_task(
            run.id,
            task.id,
            actor=task.assigned_agent or DEMO_ACTOR,
            reason="Integrity-checked synthetic demo outputs stored.",
            correlation_id=correlation_id,
        )

    orchestration.mark_task_ready(
        run.id,
        synthesis_task.id,
        actor="workflow-controller",
        reason="Evidence and impact dependencies are complete.",
        correlation_id=correlation_id,
    )
    orchestration.start_task(
        run.id,
        synthesis_task.id,
        actor="executive-orchestrator",
        reason="Synthesize deterministic demonstration result.",
        correlation_id=correlation_id,
    )
    _record_attempt_and_route(
        orchestration,
        run.id,
        synthesis_task.id,
        case_id=case["case_id"],
        route_task_id=None,
        role="liability synthesis",
    )
    _write_executive_outputs(
        registration,
        case,
        run.id,
        synthesis_task_id=synthesis_task.id,
    )
    orchestration.complete_task(
        run.id,
        synthesis_task.id,
        actor="executive-orchestrator",
        reason="Nine-artifact decision bundle is integrity checked.",
        correlation_id=correlation_id,
    )

    approval = orchestration.request_approval(
        run.id,
        requested_by="workflow-controller",
        actor="workflow-controller",
        reason=(
            "请确认已核对证据链、缺失证据和演示边界。该结果为合成演示，"
            "不得作为交警执法、司法裁判或保险理赔的自动结论。"
        ),
        expires_at=utc_now() + timedelta(days=7),
        correlation_id=correlation_id,
        metadata={
            "demo_case_id": case["case_id"],
            "reviewer_required": "founder",
            "policy_rule_ids": [
                "TRAFFIC-HUMAN-REVIEW-001",
                "DEMO-NON-AUTHORITATIVE-001",
            ],
            "synthetic": True,
            "authoritative": False,
            "inference_mode": DEMO_INFERENCE_MODE,
            "adapter_status": DEMO_ADAPTER_STATUS,
        },
    )

    snapshot = orchestration.get_snapshot(run.id)
    if RunStatus(snapshot.run.status) != RunStatus.WAITING_APPROVAL:
        raise TrafficLiabilityDemoError("Demo Run did not reach approval wait")
    if any(TaskStatus(task.status) != TaskStatus.COMPLETED for task in snapshot.tasks):
        raise TrafficLiabilityDemoError("Demo tasks did not complete")
    if ApprovalStatus(approval.approval.status) != ApprovalStatus.PENDING:
        raise TrafficLiabilityDemoError("Demo approval is not pending")
    if {artifact.name for artifact in snapshot.artifacts} != REQUIRED_ARTIFACTS:
        raise TrafficLiabilityDemoError("Demo decision bundle is incomplete")

    return TrafficLiabilityDemoResult(
        run_id=run.id,
        approval_id=approval.approval.id,
        data_dir=root,
        case_id=case["case_id"],
        created=True,
    )
