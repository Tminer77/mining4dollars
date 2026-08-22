"""Domain rules for optimizer plans."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from uuid import uuid4

import pytest

from m4d.domain.antivirus import (
    Finding,
    FindingCategory,
    NewFinding,
    classify_finding,
    materialise_finding,
)
from m4d.domain.endpoints import (
    Endpoint,
    EndpointPlatform,
    EndpointRole,
    EndpointStatus,
    NewEndpoint,
)
from m4d.domain.errors import ConflictError
from m4d.domain.optimizers import (
    ActionKind,
    OptimizerCategory,
    PlanStatus,
    propose_plan,
)

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)


def miner() -> Endpoint:
    return NewEndpoint(
        hostname="rig-01",
        platform=EndpointPlatform.LINUX,
        role=EndpointRole.MINER,
    ).materialise(now=NOW)


def finding(*, indicator: str, category: FindingCategory = FindingCategory.SUSPICIOUS) -> Finding:
    request = NewFinding(
        scan_id=uuid4(),
        endpoint_id=miner().id,
        category=category,
        indicator=indicator,
        title=indicator,
    )
    return materialise_finding(request, classification=classify_finding(request), now=NOW)


class TestPropose:
    def test_clean_miner_gets_thermal_and_power(self) -> None:
        plan = propose_plan(miner(), (), now=NOW)
        kinds = {action.kind for action in plan.actions}
        assert ActionKind.GPU_THERMAL_PROFILE in kinds
        assert ActionKind.GPU_POWER_LIMIT in kinds
        assert plan.category is OptimizerCategory.PERFORMANCE
        assert plan.status is PlanStatus.PROPOSED

    def test_malware_outranks_performance(self) -> None:
        infected = miner()
        plan = propose_plan(infected, (finding(indicator="family:emotet"),), now=NOW)
        kinds = {action.kind for action in plan.actions}
        assert ActionKind.AV_UPDATE_SIGNATURES in kinds
        assert ActionKind.AV_SCHEDULE_FULL_SCAN in kinds
        assert plan.category is OptimizerCategory.SECURITY
        assert ActionKind.GPU_POWER_LIMIT not in kinds

    def test_quarantined_miner_does_not_get_performance_actions(self) -> None:
        isolated = miner().quarantine(reason="malware", now=NOW)
        plan = propose_plan(isolated, (finding(indicator="family:emotet"),), now=NOW)
        kinds = {action.kind for action in plan.actions}
        assert ActionKind.GPU_POWER_LIMIT not in kinds
        assert ActionKind.AV_UPDATE_SIGNATURES in kinds

    def test_retiring_endpoint_is_rejected(self) -> None:
        retiring = replace(miner(), status=EndpointStatus.RETIRING)
        with pytest.raises(ConflictError, match="retiring"):
            propose_plan(retiring, (), now=NOW)


class TestPlanLifecycle:
    def test_apply_from_proposed_is_a_shortcut(self) -> None:
        plan = propose_plan(miner(), (), now=NOW)
        applied = plan.apply(now=NOW, endpoint=miner())
        assert applied.status is PlanStatus.APPLIED
        assert all(action.status.value == "applied" for action in applied.actions)

    def test_cannot_apply_performance_while_quarantined(self) -> None:
        box = miner()
        plan = propose_plan(box, (), now=NOW)
        isolated = box.quarantine(reason="malware", now=NOW)
        with pytest.raises(ConflictError, match="isolated"):
            plan.apply(now=NOW, endpoint=isolated)

    def test_rejected_plan_cannot_be_applied(self) -> None:
        plan = propose_plan(miner(), (), now=NOW).reject(now=NOW)
        with pytest.raises(ConflictError, match="rejected"):
            plan.apply(now=NOW, endpoint=miner())

    def test_accept_then_apply(self) -> None:
        plan = propose_plan(miner(), (), now=NOW).accept(now=NOW)
        applied = plan.apply(now=NOW, endpoint=miner())
        assert applied.status is PlanStatus.APPLIED
        assert applied.decided_at == NOW
