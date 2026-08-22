"""Domain rules for scans, findings, and classification policy."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from m4d.domain.antivirus import (
    AUTO_QUARANTINE_CONFIDENCE,
    Finding,
    FindingCategory,
    FindingStatus,
    NewFinding,
    NewScan,
    ScanKind,
    ScanStatus,
    classify_finding,
    materialise_finding,
)
from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.events import EventSeverity

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
ENDPOINT = uuid4()


def queued() -> NewScan:
    return NewScan(endpoint_id=ENDPOINT, kind=ScanKind.FULL)


class TestScanLifecycle:
    def test_starts_queued(self) -> None:
        scan = queued().materialise(now=NOW)
        assert scan.status is ScanStatus.QUEUED
        assert scan.findings_count == 0

    def test_start_then_complete(self) -> None:
        scan = queued().materialise(now=NOW).start(now=NOW)
        done = scan.complete(now=NOW, files_examined=12, findings_count=1)
        assert done.status is ScanStatus.COMPLETED
        assert done.files_examined == 12

    def test_cannot_complete_while_queued(self) -> None:
        scan = queued().materialise(now=NOW)
        with pytest.raises(ConflictError, match="running"):
            scan.complete(now=NOW, files_examined=0, findings_count=0)

    def test_cannot_attach_findings_while_queued(self) -> None:
        with pytest.raises(ConflictError, match="before the scan has started"):
            queued().materialise(now=NOW).with_finding_recorded()

    def test_fail_from_running(self) -> None:
        scan = queued().materialise(now=NOW).start(now=NOW).fail(now=NOW, error_message="disk")
        assert scan.status is ScanStatus.FAILED
        assert scan.error_message == "disk"

    def test_rejects_negative_files_examined(self) -> None:
        scan = queued().materialise(now=NOW).start(now=NOW)
        with pytest.raises(ValidationError, match="files_examined"):
            scan.complete(now=NOW, files_examined=-1, findings_count=0)


class TestFindingDisposition:
    def _finding(self, *, status: FindingStatus = FindingStatus.OPEN) -> Finding:
        request = NewFinding(
            scan_id=uuid4(),
            endpoint_id=ENDPOINT,
            category=FindingCategory.SUSPICIOUS,
            indicator="path:/tmp/x",
            title="odd binary",
        )
        finding = materialise_finding(request, classification=classify_finding(request), now=NOW)
        if status is finding.status:
            return finding
        return finding.dispose(status=status, now=NOW)

    def test_open_can_become_quarantined(self) -> None:
        finding = self._finding()
        assert finding.dispose(status=FindingStatus.QUARANTINED, now=NOW).status is (
            FindingStatus.QUARANTINED
        )

    def test_resolved_is_terminal(self) -> None:
        finding = self._finding().dispose(status=FindingStatus.RESOLVED, now=NOW)
        with pytest.raises(ConflictError):
            finding.dispose(status=FindingStatus.OPEN, now=NOW)

    def test_same_status_is_not_a_transition(self) -> None:
        finding = self._finding()
        with pytest.raises(ConflictError):
            finding.dispose(status=FindingStatus.OPEN, now=NOW)


class TestClassifier:
    def test_eicar_auto_quarantines(self) -> None:
        classification = classify_finding(
            NewFinding(
                scan_id=uuid4(),
                endpoint_id=ENDPOINT,
                category=FindingCategory.SUSPICIOUS,
                indicator="eicar-standard-test-file",
                title="test sample",
            )
        )
        assert classification.category is FindingCategory.MALWARE
        assert classification.auto_quarantines is True
        assert classification.confidence >= AUTO_QUARANTINE_CONFIDENCE

    def test_family_prefix_is_malware(self) -> None:
        classification = classify_finding(
            NewFinding(
                scan_id=uuid4(),
                endpoint_id=ENDPOINT,
                category=FindingCategory.SUSPICIOUS,
                indicator="family:emotet",
                title="loader",
            )
        )
        assert classification.category is FindingCategory.MALWARE
        assert classification.recommended_status is FindingStatus.QUARANTINED

    def test_cve_is_a_vulnerability_not_an_infection(self) -> None:
        classification = classify_finding(
            NewFinding(
                scan_id=uuid4(),
                endpoint_id=ENDPOINT,
                category=FindingCategory.SUSPICIOUS,
                indicator="cve-2024-1234",
                title="openssl",
            )
        )
        assert classification.category is FindingCategory.VULNERABILITY
        assert classification.auto_quarantines is False

    def test_misconfig_stays_open_for_the_optimizer(self) -> None:
        classification = classify_finding(
            NewFinding(
                scan_id=uuid4(),
                endpoint_id=ENDPOINT,
                category=FindingCategory.MISCONFIGURATION,
                indicator="cfg:ssh.password_auth",
                title="password auth enabled",
            )
        )
        assert classification.category is FindingCategory.MISCONFIGURATION
        assert classification.recommended_status is FindingStatus.OPEN

    def test_agent_can_raise_severity_but_not_drop_it(self) -> None:
        raised = classify_finding(
            NewFinding(
                scan_id=uuid4(),
                endpoint_id=ENDPOINT,
                category=FindingCategory.PUA,
                indicator="pua:toolbar",
                title="toolbar",
                severity=EventSeverity.ERROR,
            )
        )
        assert raised.severity is EventSeverity.ERROR

        kept = classify_finding(
            NewFinding(
                scan_id=uuid4(),
                endpoint_id=ENDPOINT,
                category=FindingCategory.MALWARE,
                indicator="family:x",
                title="x",
                severity=EventSeverity.INFO,
            )
        )
        assert kept.severity is EventSeverity.CRITICAL

    def test_materialise_quarantines_only_when_policy_clears_the_bar(self) -> None:
        request = NewFinding(
            scan_id=uuid4(),
            endpoint_id=ENDPOINT,
            category=FindingCategory.MALWARE,
            indicator="family:emotet",
            title="loader",
        )
        finding = materialise_finding(request, classification=classify_finding(request), now=NOW)
        assert finding.status is FindingStatus.QUARANTINED
        assert finding.ai_confidence >= AUTO_QUARANTINE_CONFIDENCE
