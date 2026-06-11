from __future__ import annotations

import pytest
from pydantic import ValidationError

from testdesigner_ai.domain.documentation import (
    BusinessAction,
    DocumentationAnalysisInput,
    DocumentationAnalysisOutput,
    EndpointClassification,
    RawEndpointMention,
    SourceDocument,
)
from testdesigner_ai.domain.evidence import EvidenceRef
from testdesigner_ai.validators.documentation import validate_endpoint_references


def scenario_input() -> DocumentationAnalysisInput:
    return DocumentationAnalysisInput(
        documents=[
            SourceDocument(
                source_id="scenario-1",
                source_type="system_analysis",
                title="Create task",
                content="1. Create a task with POST /tasks.",
            )
        ],
        endpoint_mentions=[
            RawEndpointMention(
                mention_id="endpoint-1",
                method="POST",
                path="/tasks",
                line_number=1,
                raw_text="POST /tasks",
            )
        ],
    )


def test_unknown_endpoint_mention_is_reported() -> None:
    output = DocumentationAnalysisOutput(
        endpoint_classifications=[
            EndpointClassification(
                mention_id="endpoint-99",
                location="step",
            )
        ]
    )

    issues = validate_endpoint_references(scenario_input(), output)

    assert len(issues) == 1
    assert issues[0].stage == "referential"
    assert issues[0].code == "unknown_endpoint_mention"
    assert issues[0].severity == "error"
    assert issues[0].path == "endpoint_classifications.0.mention_id"


def test_known_endpoint_mention_is_accepted() -> None:
    output = DocumentationAnalysisOutput(
        endpoint_classifications=[
            EndpointClassification(
                mention_id="endpoint-1",
                location="step",
            )
        ]
    )

    assert validate_endpoint_references(scenario_input(), output) == []


def test_output_classifies_endpoint_without_rewriting_it() -> None:
    output = DocumentationAnalysisOutput(
        actions=[
            BusinessAction(
                action_id="action-1",
                document_step_id="step-1",
                order=1,
                text="Create a task.",
                evidence=[
                    EvidenceRef(
                        source_id="scenario-1",
                        locator="line:1",
                        excerpt="Create a task",
                    )
                ],
            )
        ],
        endpoint_classifications=[
            EndpointClassification(
                mention_id="endpoint-1",
                related_action_id="action-1",
                location="step",
            )
        ],
    )

    classification = output.endpoint_classifications[0].model_dump()

    assert "method" not in classification
    assert "path" not in classification


def test_business_action_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        BusinessAction(
            action_id="action-1",
            document_step_id="step-1",
            order=1,
            text="Create a task.",
        )

