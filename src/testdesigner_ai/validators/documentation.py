from __future__ import annotations

from testdesigner_ai.domain.documentation import (
    DocumentationAnalysisInput,
    DocumentationAnalysisOutput,
    ValidationIssue,
)


def validate_endpoint_references(
    task: DocumentationAnalysisInput,
    output: DocumentationAnalysisOutput,
) -> list[ValidationIssue]:
    """Report endpoint classifications that reference unknown input mentions."""

    known_mentions = {mention.mention_id for mention in task.endpoint_mentions}
    issues: list[ValidationIssue] = []

    for index, classification in enumerate(output.endpoint_classifications):
        if classification.mention_id in known_mentions:
            continue
        issues.append(
            ValidationIssue(
                stage="referential",
                code="unknown_endpoint_mention",
                severity="error",
                message=(
                    f"Endpoint mention {classification.mention_id!r} "
                    "is absent from the analysis input."
                ),
                path=f"endpoint_classifications.{index}.mention_id",
            )
        )

    return issues
