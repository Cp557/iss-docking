"""Test Jev's guarded braking decision from a settled near-port start."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from src.agents.jev_braking import ARRIVAL_QUESTION, guarded_brake_decision, projected_state
from src.agents.jev_client import ask_jev
from src.browser.simulator import begin_episode_fast, disable_gpu_rendering, open_simulator
from src.evaluation.scenarios import (
    NEAR_DOCKING_SCENARIO,
    Scenario,
    apply_scenario,
    random_near_docking_scenario,
)
from src.recording import save_episode
from src.telemetry.state import READ_STATE


ROOT = Path(__file__).resolve().parents[1]
SERVO_PATH = ROOT / "src/agents/jev_servo.js"
FRAME_BUDGET = 1500


def step_duration_ms(distance: float) -> int:
    if distance < 0.5:
        return 50
    if distance < 2:
        return 167
    return 500


def run(max_calls: int, max_seconds: int, scenario: Scenario, output_path: Path) -> str:
    decisions = []
    braking_committed = False
    stop_reason = "CALL_LIMIT"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.set_default_timeout(60000)
            page.set_default_navigation_timeout(60000)
            page.clock.install()
            open_simulator(page)
            page.evaluate(SERVO_PATH.read_text())
            begin_episode_fast(page)
            disable_gpu_rendering(page)
            page.clock.pause_at(time.time() + 1)
            page.evaluate("""() => {
                window.__jevServo.mode = 'velocity';
                window.__jevServo.phase = 'velocity';
                window.__jevServo.targetVelocity = {
                    approach: -0.02, lateral_y: 0, lateral_z: 0
                };
            }""")
            apply_scenario(page, scenario, arm_jev=True)
            page.evaluate("window.__jevFrame = 0")
            started = time.monotonic()
            print(f"scenario={json.dumps(scenario.as_dict())}", flush=True)

            for call in range(1, max_calls + 1):
                if time.monotonic() - started >= max_seconds:
                    stop_reason = "WALL_TIME_LIMIT"
                    break
                telemetry = page.evaluate(READ_STATE)
                if telemetry["game_over"]:
                    break
                if telemetry["frame"] >= FRAME_BUDGET:
                    stop_reason = "FRAME_BUDGET"
                    break
                distance = telemetry["position_error_m"]["approach"]
                step_ms = step_duration_ms(distance)
                if braking_committed:
                    page.clock.run_for(step_ms)
                    continue

                state = projected_state({
                    "distance_m": distance,
                    "velocity_m_per_frame": telemetry["velocity_m_per_frame"]["approach"],
                    "elapsed_frames": telemetry["frame"],
                })
                try:
                    answer = ask_jev(state, ARRIVAL_QUESTION)["answers"]["switch_viable"]
                except (URLError, TimeoutError):
                    answer = ask_jev(state, ARRIVAL_QUESTION)["answers"]["switch_viable"]
                probability = answer["noul"]
                braking_committed, choice, reason = guarded_brake_decision(
                    state, probability, step_ms
                )
                target_speed = -0.0025 if braking_committed else -0.02
                page.evaluate(
                    "speed => { window.__jevServo.targetVelocity.approach = speed; }",
                    target_speed,
                )
                decisions.append({
                    "call": call,
                    "frame": telemetry["frame"],
                    "state": state,
                    "choice": choice,
                    "reason": reason,
                    "jev_yes_probability": probability,
                    "target_velocity_m_per_frame": target_speed,
                })
                page.clock.run_for(step_ms)
                if call == 1 or call % 10 == 0 or braking_committed:
                    updated = page.evaluate(READ_STATE)
                    print(
                        f"call={call} frame={updated['frame']} "
                        f"distance={updated['position_error_m']['approach']:+.3f} "
                        f"velocity={updated['velocity_m_per_frame']['approach']:+.4f} "
                        f"choice={choice}",
                        flush=True,
                    )
                    save_episode(page, decisions, "IN_PROGRESS", scenario, output_path)

            result = page.evaluate("window.__jevServo.result")
            outcome = result["outcome"] if result else stop_reason
            final = save_episode(page, decisions, outcome, scenario, output_path)
            print(
                f"outcome={outcome} calls={len(decisions)} frame={final['frame']} "
                f"distance={final['position_error_m']['approach']:+.4f} "
                f"message={final['fail_message']!r}",
                flush=True,
            )
            print(f"decisions={output_path}", flush=True)
            return outcome
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, help="repeatable near-docking variation")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-calls", type=int, default=200)
    parser.add_argument("--max-seconds", type=int, default=360)
    args = parser.parse_args()
    scenario = (
        random_near_docking_scenario(args.seed)
        if args.seed is not None else NEAR_DOCKING_SCENARIO
    )
    name = f"jev_braking_seed_{args.seed}.json" if args.seed is not None else "jev_braking_default.json"
    output_path = args.output or ROOT / "results" / name
    try:
        return 0 if run(args.max_calls, args.max_seconds, scenario, output_path) == "SUCCESS" else 1
    except HTTPError as error:
        print(f"Jev API returned HTTP {error.code}", file=sys.stderr)
    except (URLError, TimeoutError) as error:
        print(f"Could not reach Jev API: {error}", file=sys.stderr)
    except (ValueError, KeyError, TypeError) as error:
        print(f"Jev braking attempt failed: {error}", file=sys.stderr)
    except PlaywrightTimeoutError as error:
        print(f"Simulator timed out: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
