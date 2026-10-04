"""Probe Jev's control limits with brake-phase or velocity decisions."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

from playwright.sync_api import sync_playwright

from scripts.dock import QUESTION, SERVO_PATH, phase_allowed
from src.agents.jev_braking import projected_state
from src.agents.jev_client import ask_jev
from src.browser.simulator import begin_episode_fast, disable_gpu_rendering, open_simulator
from src.evaluation.scenarios import DEFAULT_SCENARIO, Scenario, apply_scenario, random_scenario
from src.recording import save_episode
from src.telemetry.state import READ_STATE


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_PHASE_QUESTION = {
    "phase": {
        "type": "choice",
        "instructions": (
            "You are piloting the final approach from the 8 m standoff. Choose one "
            "phase now. Your goal is to dock below 0.2 m with each attitude error "
            "below 0.2 degrees and each velocity component below 0.004 m/frame. "
            "Capture targets -0.02 m/frame toward the port. Brake targets "
            "-0.0025 m/frame. Use the supplied braking-distance and arrival "
            "estimates to decide when to brake. There is no automatic braking "
            "override in this experiment. Once braking starts, do not accelerate again."
        ),
        "criteria": {
            "capture": "Continue closing at -0.02 m/frame.",
            "brake": "Switch to -0.0025 m/frame and keep that target until docking.",
            "hold": "Stop if the approach is unsafe.",
        },
    }
}
BRAKING_PHASE_QUESTION = {
    "phase": {
        "type": "choice",
        "instructions": (
            "You have already started braking for docking. Continue at the "
            "-0.0025 m/frame target unless the approach is unsafe."
        ),
        "criteria": {
            "brake": "Keep closing at docking speed.",
            "hold": "Stop if the approach is unsafe.",
        },
    }
}

APPROACH_SPEEDS = {
    "stop": 0.0,
    "fast": -0.2,
    "medium": -0.05,
    "slow": -0.02,
    "creep": -0.0025,
    "retreat": 0.02,
}
LATERAL_SPEEDS = {
    "negative_fast": -0.04,
    "negative_slow": -0.01,
    "negative_creep": -0.0025,
    "stop": 0.0,
    "positive_creep": 0.0025,
    "positive_slow": 0.01,
    "positive_fast": 0.04,
}


def velocity_questions(mode: str) -> dict:
    goal = (
        "You are piloting an ISS docking using three world-axis velocity targets. "
        "Choose all three targets now. The actuator tracks your targets but does "
        "not plan the path. A target opposite the sign of its position error "
        "reduces that error. Allow attitude to settle before a fast approach. "
        "Dock below 0.2 m with each attitude error below 0.2 degrees and each "
        "velocity component below 0.004 m/frame. There is no phase planner or "
        "automatic braking override in this experiment."
    )
    if mode != "velocity":
        goal += (
            " Aim to dock by frame 5000. The state gives travel-time estimates "
            "for each target speed. A creep-speed approach from 200 m takes "
            "roughly 80,000 frames, so use faster targets while there is room, "
            "then slow in time for a safe docking."
        )
    if mode == "velocity_instructed":
        goal += (
            " Keep approach velocity at zero until attitude and lineup are ready. "
            "If lineup is lost inside 8 m, stop approach and correct lateral error. "
            "Choose lateral velocities opposite the corresponding position errors; "
            "use smaller magnitudes near zero and stop within 0.04 m. "
            "In the final 8 m, reduce approach speed to -0.02 m/frame, then switch "
            "to -0.0025 before braking room can be consumed by the next decision "
            "interval. Once at -0.0025, never accelerate again. The state gives "
            "braking room and distance closed before the next decision."
        )
    return {
        "approach": {
            "type": "choice",
            "instructions": f"{goal} Choose approach velocity in m/frame toward the port.",
            "criteria": {name: f"Target {speed:+g} m/frame."
                         for name, speed in APPROACH_SPEEDS.items()},
        },
        **{
            axis: {
                "type": "choice",
                "instructions": f"{goal} Choose {axis} velocity in m/frame.",
                "criteria": {name: f"Target {speed:+g} m/frame."
                             for name, speed in LATERAL_SPEEDS.items()},
            }
            for axis in ("lateral_y", "lateral_z")
        },
    }


def ask_with_retry(state: dict, questions: dict) -> dict:
    try:
        return ask_jev(state, questions)["answers"]
    except (URLError, TimeoutError):
        return ask_jev(state, questions)["answers"]


def travel_frames(error: float, boundary: float, speeds: dict[str, float]) -> dict:
    distance = max(0, abs(error) - boundary)
    return {name: round(distance / abs(speed))
            for name, speed in speeds.items() if speed and speed * error < 0}


def run(mode: str, max_calls: int, max_seconds: int,
        scenario: Scenario, output_path: Path) -> str:
    decisions = []
    capture_start_frame = None
    braking_started = False
    questions = velocity_questions(mode) if mode != "phase" else None

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
            if mode != "phase":
                page.evaluate("""() => {
                    window.__jevServo.mode = 'velocity';
                    window.__jevServo.phase = 'velocity';
                }""")
            started = time.monotonic()
            print(f"mode={mode} scenario={json.dumps(scenario.as_dict())}", flush=True)

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
                        "phase": snapshot["phase"],
                        "position_m": initial["position_error_m"],
                        "velocity_m_per_frame": initial["velocity_m_per_frame"],
                        "attitude_deg": initial["angle_deg"],
                        "range_m": initial["range_m"],
                        "ready": snapshot["ready"],
                    }

                if mode == "phase":
                    jev_state = {
                        "goal": "Dock safely with the ISS docking port.",
                        "current_phase": snapshot["phase"],
                        "ready": snapshot["ready"],
                        "telemetry": state,
                        "phase_effects": (
                            "align settles attitude; lineup centers laterally; "
                            "cruise approaches and stops at 8 m; capture closes "
                            "the final 8 m; brake closes at docking speed."
                        ),
                    }
                    if capture_start_frame is not None:
                        jev_state["braking_estimate"] = projected_state(
                            {
                                "distance_m": state["position_m"]["approach"],
                                "velocity_m_per_frame": state["velocity_m_per_frame"]["approach"],
                                "elapsed_frames": snapshot["frame"],
                            },
                            deadline_frame=capture_start_frame + 1500,
                        )
                    question = (BRAKING_PHASE_QUESTION if braking_started
                                else CAPTURE_PHASE_QUESTION if capture_start_frame is not None
                                else QUESTION)
                    answer = ask_with_retry(jev_state, question)["phase"]
                    choice = answer["choice"]
                    if choice not in question["phase"]["criteria"]:
                        raise ValueError(f"Unexpected Jev phase: {choice}")
                    allowed = (phase_allowed(choice, snapshot["phase"], snapshot["ready"])
                               if capture_start_frame is None else True)
                    applied = choice if allowed else "hold"
                    if applied == "capture" and capture_start_frame is None:
                        capture_start_frame = snapshot["frame"]
                    if applied == "brake":
                        braking_started = True
                    page.evaluate("phase => { window.__jevServo.phase = phase; }", applied)
                    decision = {
                        "call": call, "frame": snapshot["frame"],
                        "current_phase": snapshot["phase"], "choice": choice,
                        "applied_phase": applied, "vetoed": not allowed,
                        "probabilities": answer["probabilities"], "state": state,
                    }
                    if jev_state.get("braking_estimate"):
                        decision["braking_estimate"] = jev_state["braking_estimate"]
                    note = f"Jev={choice} applied={applied}"
                else:
                    jev_state = {
                        "goal": "Dock safely with the ISS docking port.",
                        "telemetry": state,
                        "braking_estimate_if_slow": projected_state(
                            {
                                "distance_m": state["position_m"]["approach"],
                                "velocity_m_per_frame": state["velocity_m_per_frame"]["approach"],
                                "elapsed_frames": snapshot["frame"],
                            },
                            deadline_frame=5000,
                        ),
                    }
                    if mode != "velocity":
                        position = state["position_m"]
                        brake_estimate = jev_state["braking_estimate_if_slow"]
                        step_ms = 500 if state["range_m"] >= 1 else 167
                        jev_state["pace"] = {
                            "goal_frame": 5000,
                            "frames_remaining": max(0, 5000 - snapshot["frame"]),
                            "approach_frames_at_target_speed": travel_frames(
                                position["approach"], 0.2, APPROACH_SPEEDS
                            ),
                            "lateral_y_frames_at_target_speed": travel_frames(
                                position["lateral_y"], 0.04, LATERAL_SPEEDS
                            ),
                            "lateral_z_frames_at_target_speed": travel_frames(
                                position["lateral_z"], 0.04, LATERAL_SPEEDS
                            ),
                        }
                        if mode == "velocity_instructed":
                            jev_state["pace"]["braking_room_m"] = round(
                                brake_estimate["distance_to_docking_boundary_m"]
                                - brake_estimate["estimated_braking_distance_m"], 4
                            )
                            jev_state["pace"]["distance_closed_before_next_decision_m"] = round(
                                max(0, -state["velocity_m_per_frame"]["approach"])
                                * ((step_ms + 15) // 16), 4
                            )
                            if decisions:
                                jev_state["previous_target_velocity"] = decisions[-1][
                                    "target_velocity_m_per_frame"
                                ]
                    answers = ask_with_retry(jev_state, questions)
                    choices = {axis: answers[axis]["choice"]
                               for axis in ("approach", "lateral_y", "lateral_z")}
                    target = {
                        "approach": APPROACH_SPEEDS[choices["approach"]],
                        "lateral_y": LATERAL_SPEEDS[choices["lateral_y"]],
                        "lateral_z": LATERAL_SPEEDS[choices["lateral_z"]],
                    }
                    page.evaluate(
                        "target => { window.__jevServo.targetVelocity = target; }", target
                    )
                    decision = {
                        "call": call, "frame": snapshot["frame"], "choices": choices,
                        "target_velocity_m_per_frame": target,
                        "probabilities": {axis: answers[axis]["probabilities"]
                                          for axis in choices},
                        "state": state,
                    }
                    note = f"Jev targets={target}"

                decisions.append(decision)
                step_ms = 500 if state["range_m"] >= 1 else 167
                page.clock.run_for(step_ms)
                if call == 1 or call % 10 == 0 or (
                    mode == "phase" and decision["applied_phase"] != snapshot["phase"]
                ):
                    updated = page.evaluate("window.__jevServo.telemetry")
                    print(
                        f"call={call} frame={updated['frame']} {note} "
                        f"range={updated['range_m']:.2f} lateral={updated['lateral_m']:.2f}",
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
    parser.add_argument("--mode", choices=("phase", "velocity", "velocity_paced",
                                          "velocity_instructed"), required=True)
    parser.add_argument("--max-calls", type=int, default=250)
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--seed", type=int, help="use a repeatable randomized start")
    args = parser.parse_args()
    scenario = random_scenario(args.seed) if args.seed is not None else DEFAULT_SCENARIO
    label = f"seed_{args.seed}" if args.seed is not None else "default"
    output_path = ROOT / f"results/experiment_{args.mode}_{label}.json"
    try:
        return 0 if run(args.mode, args.max_calls, args.max_seconds,
                        scenario, output_path) == "SUCCESS" else 1
    except HTTPError as error:
        print(f"Jev API returned HTTP {error.code}", file=sys.stderr)
    except (URLError, TimeoutError) as error:
        print(f"Could not reach Jev API: {error}", file=sys.stderr)
    except (ValueError, KeyError, TypeError) as error:
        print(f"Jev experiment failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
