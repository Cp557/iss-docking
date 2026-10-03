"""Save a simulator episode with Jev decisions and every control action."""

import json
from pathlib import Path

from src.evaluation.scenarios import Scenario
from src.telemetry.state import READ_STATE


def save_episode(
    page, decisions: list[dict], outcome: str, scenario: Scenario, output_path: Path
) -> dict:
    snapshot = page.evaluate("""() => ({
        trace: window.__jevServo.trace,
        result: window.__jevServo.result,
    })""")
    final = page.evaluate(READ_STATE)
    data = {
        "scenario": scenario.as_dict(),
        "outcome": outcome,
        "decisions": decisions,
        "records": snapshot["trace"],
        "result": snapshot["result"],
        "final": final,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2))
    return final
