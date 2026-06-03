from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from domain import AgentRun, ProjectState, RawEndpointMention, ScenarioInput


ENDPOINT_PATTERN = re.compile(
    r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE)\s+)?"
    r"(?P<path>/[A-Za-z0-9_{}.-]+(?:/[A-Za-z0-9_{}.-]+)*)",
    re.IGNORECASE,
)


def load_scenario(path: str) -> ScenarioInput:
    scenario_path = Path(path)
    text = scenario_path.read_text(encoding="utf-8")
    title = scenario_path.stem
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("#"):
            title = cleaned.strip("# ").strip()
            break
    return ScenarioInput(
        path=str(scenario_path),
        title=title,
        text=text,
        raw_endpoint_mentions=extract_raw_endpoint_mentions(text),
    )


def extract_raw_endpoint_mentions(text: str) -> list[RawEndpointMention]:
    mentions: list[RawEndpointMention] = []
    seen: set[tuple[str | None, str, int]] = set()

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in ENDPOINT_PATTERN.finditer(line):
            method = match.group("method")
            path = match.group("path").rstrip(".,;)")
            key = (method.upper() if method else None, path, line_number)
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                RawEndpointMention(
                    method=method.upper() if method else None,
                    path=path,
                    raw_text=match.group(0),
                    line_number=line_number,
                )
            )
    return mentions


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_test_data(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data_path = Path(path)
    if data_path.suffix.lower() == ".json":
        data = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        data = load_yaml(data_path)
    if not isinstance(data, dict):
        raise ValueError("Test data file must contain a JSON/YAML object at top level.")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def reset_log(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "run.log").write_text("", encoding="utf-8")

    def log_event(self, message: str, **fields: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        details = ""
        if fields:
            details = " | " + " ".join(f"{key}={value}" for key, value in fields.items())
        with (self.root / "run.log").open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] {message}{details}\n")

    def save_state(self, state: ProjectState) -> None:
        write_json(self.root / "state.json", state.model_dump(mode="json"))

    def save_json(self, relative_path: str, data: Any) -> None:
        write_json(self.root / relative_path, data)

    def save_agent_run(self, run: AgentRun, artifact_name: str | None = None) -> None:
        safe_name = artifact_name or run.agent_name.lower().replace(" ", "_")
        write_json(self.root / f"{safe_name}.run.json", run.model_dump(mode="json"))
        prompt_text = "\n\n".join(
            f"## {message.role.upper()}\n{message.content}" for message in run.prompt
        )
        write_text(self.root / f"{safe_name}.prompt.md", prompt_text)
