"""Project and system memory: QA misses are stored, then handed to the next run."""

from pathlib import Path

from app.services.agent_memory import (
    lessons_for_agent,
    project_memory_path,
    record_qa_result,
    system_memory_path,
    with_memory,
)
from app.services.phase_validation.schema import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    ValidationResult,
)


def _result() -> ValidationResult:
    return ValidationResult(
        agent_key="search_demand",
        phase_label="Search Demand & Keywords",
        iteration=1,
        decision="needs_revision",
        summary="Service seeds missed the client's services.",
        checks=[
            CheckResult(
                check_id="qa_service_scope",
                parameter="service_product_alignment",
                category="service_product_alignment",
                status=CheckStatus.FAILED.value,
                severity=CheckSeverity.MAJOR.value,
                message="None of the services on this deliverable overlap the client's services.",
                evidence="Click Trends private note",
                recommended_correction="Seed only the selected services.",
                layer="scope",
            )
        ],
    )


def test_a_qa_miss_is_logged_on_the_project_and_folded_into_system_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_memory.memory_root",
        lambda: Path(tmp_path),
    )
    record_qa_result(client_id="client-1", result=_result())
    record_qa_result(client_id="client-2", result=_result())

    project = project_memory_path("client-1").read_text(encoding="utf-8")
    system = system_memory_path().read_text(encoding="utf-8")
    assert "qa_service_scope" in project
    assert "Click Trends private note" in project
    assert "client-1" not in system
    assert "Click Trends" not in system
    assert '"seen": 2' in system
    assert project_memory_path("client-1").with_suffix(".md").exists()
    assert system_memory_path().with_suffix(".md").exists()


def test_the_next_run_is_told_to_act_on_the_stored_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_memory.memory_root",
        lambda: Path(tmp_path),
    )
    record_qa_result(client_id="client-1", result=_result())

    lessons = lessons_for_agent(client_id="client-1", agent_key="search_demand")
    assert lessons[0]["reason"].startswith("None of the services")
    assert lessons[0]["scope"] == "project"

    message = with_memory("Run keyword research", client_id="client-1", agent_key="search_demand")
    assert message.startswith("Run keyword research")
    assert "do not repeat these QA misses" in message
    assert "Seed only the selected services." in message

    untouched = with_memory("Run discovery", client_id="client-1", agent_key="discovery_agent")
    assert untouched == "Run discovery"


def test_a_clean_pass_is_not_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_memory.memory_root",
        lambda: Path(tmp_path),
    )
    record_qa_result(
        client_id="client-1",
        result=ValidationResult(
            agent_key="search_demand",
            phase_label="Search Demand & Keywords",
            iteration=1,
            decision="pass",
            checks=[],
        ),
    )
    assert not project_memory_path("client-1").exists()
    assert not system_memory_path().exists()
