"""
Human Review — LangGraph interrupt после Diagnosis.

Принцип 3.5: если `needs_human=True` хотя бы в одном диагнозе — граф
останавливается и ждёт решения от человека прежде чем двигаться к Test Designer.

Человек может:
  - approve   → принять диагноз, продолжить тест-дизайн
  - reclassify → указать другую категорию
  - fix_card  → сообщить что FlowCard надо поправить (Test Designer пропускается)
  - skip      → пропустить этот диагноз

Граф в автоматическом режиме (no_interrupt=True) пропускает узел целиком.
"""

from langgraph.types import interrupt

from src.state import GraphState


def human_review(state: GraphState) -> dict:
    """
    Останавливает граф для ревью если есть диагнозы с needs_human=True.

    При resume ожидает список решений вида:
    [
        {
            "case_id": "...",
            "step_id": "...",
            "decision": "approve" | "reclassify" | "fix_card" | "skip",
            "note": "optional human comment",
            "new_category": "..."  # только при decision=reclassify
        },
        ...
    ]
    """
    # Автоматический режим — пропускаем
    if state.get("no_interrupt"):
        return {"trace": ["human_review"]}

    diagnoses: list[dict] = state.get("diagnoses", [])
    needs_review = [d for d in diagnoses if d.get("needs_human")]

    if not needs_review:
        return {"trace": ["human_review"]}

    # Interrupt — граф паузируется здесь, ожидает Command(resume=decisions)
    human_decisions: list[dict] = interrupt({
        "type": "human_review_required",
        "message": (
            f"{len(needs_review)} diagnosis(es) need human review. "
            "For each, provide: decision (approve/reclassify/fix_card/skip), "
            "optional note, optional new_category."
        ),
        "diagnoses_needing_review": [
            {
                "case_id": d.get("case_id"),
                "step_id": d.get("step_id"),
                "category": d.get("category"),
                "confidence": d.get("confidence"),
                "evidence": d.get("evidence", []),
                "reasoning": d.get("reasoning", ""),
            }
            for d in needs_review
        ],
    })

    # Применяем решения к diagnoses
    decisions_map = {
        (dec.get("case_id"), dec.get("step_id")): dec
        for dec in (human_decisions or [])
    }
    updated_diagnoses = []
    for d in diagnoses:
        key = (d.get("case_id"), d.get("step_id"))
        dec = decisions_map.get(key)
        if dec:
            d = dict(d)  # копия
            d["human_decision"] = dec.get("decision", "approve")
            d["human_note"] = dec.get("note", "")
            if dec.get("new_category"):
                d["category"] = dec["new_category"]
        updated_diagnoses.append(d)

    return {
        "diagnoses": updated_diagnoses,
        "trace": ["human_review"],
    }
