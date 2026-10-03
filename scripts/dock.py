"""Dock with Jev choosing flight phases and a guarded braking point."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

from playwright.sync_api import sync_playwright

from src.agents.jev_braking import (
    CAPTURE_ARRIVAL_QUESTION,
    guarded_brake_decision,
    projected_state,
)
from src.agents.jev_client import ask_jev
from src.browser.simulator import (
    begin_episode_fast,
    disable_gpu_rendering,
    open_simulator,
)
from src.evaluation.scenarios import DEFAULT_SCENARIO, Scenario, apply_scenario, random_scenario
from src.recording import save_episode
from src.telemetry.state import READ_STATE


ROOT = Path(__file__).resolve().parents[1]
SERVO_PATH = ROOT / "src/agents/jev_servo.js"
PHASES = {"hold", "align", "lineup", "cruise", "capture"}
QUESTION = {
    "phase": {
        "type": "choice",
        "instructions": (
            "You are the mission decision maker for an ISS docking. Choose the single "
            "flight phase to command now. A fast frame-level actuator will track your "
            "phase while you are paused. Advance through align, lineup, cruise, and "
            "capture when the readiness flags permit. Continue capture once started "
            "if the path remains safe. Your goal is to reach the docking port with "
            "range below 0.2 m, attitude below 0.2 degrees, and speed below "
            "0.004 m/frame. Do not stay in hold when a safe phase can make progress."
        ),
        "criteria": {
            "hold": "Pause approach motion if telemetry indicates danger or uncertainty.",
            "align": "Settle roll, pitch, and yaw when attitude_ready is false.",
            "lineup": "Center lateral position when attitude_ready is true and lineup_ready is false.",
            "cruise": "Approach and stop at 8 m when lineup_ready is true and standoff_ready is false.",
            "capture": "Close the final 8 m when standoff_ready is true, or continue an active safe capture.",
        },
    }
}
CAPTURE_QUESTION = {
    "phase": {
        "type": "choice",
        "instructions": (
            "You already authorized the final capture from the 8 m standoff. "
            "Choose whether to continue closing toward docking or stop for safety. "
            "The 8 m standoff_ready flag naturally becomes false after closing starts; "
            "that is expected and is not a reason to reverse. Your goal remains safe docking."
        ),
        "criteria": {
            "capture": "Continue the active final approach while attitude and lateral alignment remain safe.",
            "hold": "Brake to a stop only if the final approach has become unsafe.",
        },
    }
}


def phase_allowed(choice: str, current: str, ready: dict) -> bool:
    if choice == "lineup":
        return ready["attitude"]
    if choice == "cruise":
        return ready["lineup"] or current == "cruise"
    if choice == "capture":
        return ready["standoff"] or current == "capture"
    return True


def run(max_calls: int, max_seconds: int, scenario: Scenario, output_path: Path) -> str:
    decisions = []
    capture_started = False
    capture_start_frame = None
    braking_committed = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            page.clock.install()
            open_simulator(page)
            page.evaluate(SERVO_PATH.read_text())
            begin_episode_fast(page)
            disable_gpu_rendering(page)
            page.clock.pause_at(time.time() + 10)
            apply_scenario(page, scenario, arm_jev=True)
            page.evaluate("window.__jevFrame = 0")
            started = time.monotonic()
            print(f"scenario={json.dumps(scenario.as_dict())}", flush=True)

            for call in range(1, max_calls + 1):
                if time.monotonic() - started >= max_seconds:
                    break
                snapshot = page.evaluate("""() => ({
                    frame: window.__jevServo.frames,
                    phase: window.__jevServo.phase,
                    ready: window.__jevServo.ready,
                    telemetry: window.__jevServo.telemetry,
                    result: window.__jevServo.result,
                })""")
                if snapshot["result"]:
                    break
                state = snapshot["telemetry"]
                if state is None:
                    initial = page.evaluate(READ_STATE)
                    state = {
                        "frame": 0,
                        "phase": "hold",
                        "position_m": initial["position_error_m"],
                        "velocity_m_per_frame": initial["velocity_m_per_frame"],
                        "attitude_deg": initial["angle_deg"],
                        "range_m": initial["range_m"],
                        "ready": snapshot["ready"],
                    }
                jev_state = {
                    "goal": "Dock safely with the ISS docking port.",
                    "current_phase": snapshot["phase"],
                    "attitude_ready": snapshot["ready"]["attitude"],
                    "lineup_ready": snapshot["ready"]["lineup"],
                    "standoff_ready": snapshot["ready"]["standoff"],
                    "capture_started": capture_started,
                    "telemetry": state,
                    "phase_effects": (
                        "align settles attitude while holding range and lateral position; "
                        "lineup centers lateral position while holding range; "
                        "cruise centers laterally and brakes to 8 m; "
                        "capture centers laterally and closes the final 8 m."
                    ),
                }
                try:
                    answer = ask_jev(
                        jev_state, CAPTURE_QUESTION if capture_started else QUESTION
                    )["answers"]["phase"]
                except (URLError, TimeoutError):
                    # Physics is paused, so a transient network error cannot move the craft.
                    answer = ask_jev(
                        jev_state, CAPTURE_QUESTION if capture_started else QUESTION
                    )["answers"]["phase"]
                choice = answer["choice"]
                if choice not in PHASES:
                    raise ValueError(f"Unexpected Jev phase: {choice}")
                allowed = phase_allowed(choice, snapshot["phase"], snapshot["ready"])
                applied = choice if allowed else "hold"
                if applied == "capture" and not capture_started:
                    capture_started = True
                    capture_start_frame = snapshot["frame"]
                page.evaluate("phase => { window.__jevServo.phase = phase; }", applied)
                decision = {
                    "call": call,
                    "frame": snapshot["frame"],
                    "current_phase": snapshot["phase"],
                    "choice": choice,
                    "applied_phase": applied,
                    "vetoed": not allowed,
                    "probabilities": answer.get("probabilities"),
                    "state": state,
                }
                step_ms = 500 if state["range_m"] >= 1 else 167
                if applied == "capture" and not braking_committed:
                    braking_state = projected_state(
                        {
                            "distance_m": state["position_m"]["approach"],
                            "velocity_m_per_frame": state["velocity_m_per_frame"]["approach"],
                            "elapsed_frames": snapshot["frame"],
                        },
                        deadline_frame=capture_start_frame + 1500,
                    )
                    try:
                        braking_answer = ask_jev(
                            braking_state, CAPTURE_ARRIVAL_QUESTION
                        )["answers"]["switch_viable"]
                    except (URLError, TimeoutError):
                        braking_answer = ask_jev(
                            braking_state, CAPTURE_ARRIVAL_QUESTION
                        )["answers"]["switch_viable"]
                    probability = braking_answer["noul"]
                    braking_committed, brake_choice, reason = guarded_brake_decision(
                        braking_state, probability, step_ms
                    )
                    target = -0.0025 if braking_committed else -0.02
                    page.evaluate(
                        "target => { window.__jevServo.captureApproachTarget = target; }",
                        target,
                    )
                    decision["braking"] = {
                        "state": braking_state,
                        "jev_yes_probability": probability,
                        "choice": brake_choice,
                        "reason": reason,
                        "target_velocity_m_per_frame": target,
                    }
                decisions.append(decision)
                page.clock.run_for(step_ms)
                if (call == 1 or call % 10 == 0 or applied != snapshot["phase"]
                        or decision.get("braking", {}).get("choice") in
                        {"switch_to_creep", "emergency_brake"}):
                    updated = page.evaluate("window.__jevServo.telemetry")
                    print(
                        f"call={call} frame={updated['frame']} Jev={choice} applied={applied} "
                        f"range={updated['range_m']:.2f} "
                        f"lateral={updated['lateral_m']:.2f} ready={updated['ready']}",
                        flush=True,
                    )
                    save_episode(page, decisions, "IN_PROGRESS", scenario, output_path)

            result = page.evaluate("window.__jevServo.result")
            outcome = result["outcome"] if result else "TIMEOUT"
            final = save_episode(page, decisions, outcome, scenario, output_path)
            print(
                f"outcome={outcome} calls={len(decisions)} frame={final['frame']} "
                f"range={final['range_m']:.4f} message={final['fail_message']!r}",
                flush=True,
            )
            print(f"decisions={output_path}", flush=True)
            return outcome
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, default=250)
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--seed", type=int, help="use a repeatable randomized start")
    args = parser.parse_args()
    scenario = random_scenario(args.seed) if args.seed is not None else DEFAULT_SCENARIO
    if args.seed is not None:
        output_path = ROOT / f"results/jev_docking_seed_{args.seed}.json"
    else:
        output_path = ROOT / "results/jev_docking_default.json"
    try:
        outcome = run(args.max_calls, args.max_seconds, scenario, output_path)
        return 0 if outcome == "SUCCESS" else 1
    except HTTPError as error:
        print(f"Jev API returned HTTP {error.code}", file=sys.stderr)
    except (URLError, TimeoutError) as error:
        print(f"Could not reach Jev API: {error}", file=sys.stderr)
    except (ValueError, KeyError, TypeError) as error:
        print(f"Jev-guided attempt failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
