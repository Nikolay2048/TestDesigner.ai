"""
Inter-flow composition: разворачивает requires_flows в линейную setup_chain.

Алгоритм:
  Топологическая сортировка (DFS, post-order) по графу зависимостей потоков.
  Транзитивные зависимости разрешаются автоматически.
  Цикл → FlowCycleError, отсутствующий поток → KeyError.

Результат: список ScenarioStep из зависимых потоков в порядке,
           который гарантирует, что предусловие выполняется до зависимого шага.
"""

from src.models.flow import FlowCard, ScenarioStep


class FlowCycleError(ValueError):
    """Circular dependency between flows."""


def topological_order(
    start_flow_id: str,
    all_flows: dict[str, FlowCard],
) -> list[str]:
    """
    Возвращает flow_id всех зависимостей start_flow_id в порядке исполнения.
    Сам start_flow_id в результат НЕ включается.

    Пример: A requires [B, C], B requires [C]
      → topological_order("A", ...) == ["C", "B"]
    """
    visited: set[str] = set()
    in_progress: set[str] = set()
    order: list[str] = []

    def _visit(flow_id: str) -> None:
        if flow_id in in_progress:
            raise FlowCycleError(
                f"Circular dependency detected: flow {flow_id!r} depends on itself transitively"
            )
        if flow_id in visited:
            return
        in_progress.add(flow_id)
        flow = all_flows.get(flow_id)
        if flow is None:
            raise KeyError(f"Required flow {flow_id!r} not found in available flows")
        for dep_id in flow.requires_flows:
            _visit(dep_id)
        in_progress.discard(flow_id)
        visited.add(flow_id)
        if flow_id != start_flow_id:
            order.append(flow_id)

    _visit(start_flow_id)
    return order


def flatten_setup_chain(
    target_flow: FlowCard,
    all_flows: dict[str, FlowCard],
) -> list[ScenarioStep]:
    """
    Возвращает полный список setup-шагов для target_flow:
    шаги из всех транзитивно необходимых потоков в порядке выполнения.

    Эти шаги выполняются ПЕРЕД шагами самого target_flow.
    Если requires_flows пуст — возвращает пустой список.
    """
    if not target_flow.requires_flows:
        return []
    order = topological_order(target_flow.flow_id, all_flows)
    setup: list[ScenarioStep] = []
    for flow_id in order:
        setup.extend(all_flows[flow_id].steps)
    return setup
