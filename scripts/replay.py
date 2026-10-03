"""Replay frame-indexed Jev actions at normal speed or verify them with a fast clock."""

import argparse
import json
import math
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.browser.simulator import (
    begin_episode,
    begin_episode_fast,
    disable_gpu_rendering,
    open_simulator,
)
from src.evaluation.scenarios import Scenario, apply_scenario
from src.telemetry.state import READ_STATE


DEFAULT_RECORDING = Path(__file__).resolve().parents[1] / "demo/recording.json"


INSTALL_REPLAY = """events => {
    const controls = {
        rollLeft, rollRight, pitchUp, pitchDown, yawLeft, yawRight,
        translateLeft, translateRight, translateUp, translateDown,
        translateForward, translateBackward,
    };
    const byFrame = new Map(events.map(event => [event.frame, event.actions]));
    const originalRender = render;
    window.__jevFrame = 0;
    window.__replayArmed = false;
    window.__replayResult = null;
    window.render = function() {
        if (window.__replayArmed) {
            for (const action of byFrame.get(window.__jevFrame) || []) controls[action]();
        }
        const wasGameOver = isGameOver;
        const deg = 180 / Math.PI;
        const x = camera.position.z - issObject.position.z;
        const y = camera.position.x - issObject.position.x;
        const z = camera.position.y - issObject.position.y;
        const preTelemetry = {
            frame: window.__jevFrame,
            position_m: { approach: x, lateral_y: y, lateral_z: z },
            velocity_m_per_frame: {
                approach: motionVector.z, lateral_y: motionVector.x, lateral_z: motionVector.y,
            },
            attitude_deg: {
                roll: camera.rotation.z * deg,
                pitch: camera.rotation.x * deg,
                yaw: camera.rotation.y * deg,
            },
            range_m: Math.hypot(x, y, z),
            lateral_m: Math.hypot(y, z),
        };
        const result = originalRender();
        if (!wasGameOver && isGameOver) {
            const message = document.querySelector('#fail-message').textContent.trim();
            window.__replayResult = {
                outcome: message ? 'FAIL' : 'SUCCESS', message, preTelemetry,
            };
        }
        if (window.__replayArmed) window.__jevFrame++;
        return result;
    };
}"""


def max_capture_difference(actual: dict, expected: dict) -> float:
    values = [abs(actual["range_m"] - expected["range_m"])]
    for field in ("position_m", "velocity_m_per_frame", "attitude_deg"):
        values.extend(abs(actual[field][axis] - value) for axis, value in expected[field].items())
    return max(values)


def replay(path: Path, headed: bool, fast: bool, fast_start: bool = False) -> bool:
    data = json.loads(path.read_text())
    records = data["records"]
    if not records:
        raise ValueError("Decision file has no actions")
    if not data.get("result"):
        raise ValueError("Decision file has no simulator result to verify")
    events = [{"frame": record["frame"], "actions": record["actions"]} for record in records]
    target_frame = data["final"]["frame"]
    scenario = Scenario(**data["scenario"])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.clock.install()
            open_simulator(page)
            (begin_episode_fast if fast_start else begin_episode)(page)
            if not headed:
                disable_gpu_rendering(page)
            page.clock.pause_at(time.time() + 1)
            page.evaluate(INSTALL_REPLAY, events)
            apply_scenario(page, scenario)
            page.evaluate("window.__jevFrame = 0; window.__replayArmed = true")

            if fast:
                while True:
                    frame = page.evaluate("window.__jevFrame")
                    if frame >= target_frame:
                        break
                    remaining = target_frame - frame
                    page.clock.run_for(1000 if remaining > 65 else (100 if remaining > 7 else 17))
            else:
                page.clock.resume()
                while page.evaluate("window.__jevFrame") < target_frame:
                    page.wait_for_timeout(200)

            actual = page.evaluate(READ_STATE)
            replay_result = page.evaluate("window.__replayResult")
            difference = max_capture_difference(
                replay_result["preTelemetry"], data["result"]["telemetry"]
            ) if replay_result else math.inf
            matches = (
                actual["frame"] == target_frame
                and replay_result is not None
                and replay_result["outcome"] == data["outcome"]
                and math.isclose(difference, 0, abs_tol=1e-3)
            )
            print(f"source_outcome={data['outcome']} replay_frame={actual['frame']} "
                  f"range={actual['range_m']:.4f} max_state_difference={difference:.6f}")
            print(f"replay_match={matches}")
            if headed:
                page.wait_for_timeout(3000)
            return matches
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_RECORDING)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--fast", action="store_true", help="advance the test clock quickly")
    parser.add_argument("--fast-start", action="store_true", help="skip the cosmetic BEGIN warp")
    args = parser.parse_args()
    return 0 if replay(args.decisions, args.headed, args.fast, args.fast_start) else 1


if __name__ == "__main__":
    raise SystemExit(main())
