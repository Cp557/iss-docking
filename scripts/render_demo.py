"""Render a short, labeled video from a recorded Jev docking run."""

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.replay import INSTALL_REPLAY, max_capture_difference
from src.telemetry.state import READ_STATE
from src.browser.simulator import begin_episode_fast, open_simulator
from src.evaluation.scenarios import Scenario, apply_scenario


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = ROOT / "demo/recording.json"
DEFAULT_OUTPUT = ROOT / "demo"
START_FRAME = 0
PREVIEW_FRAMES = (START_FRAME, 180, 530, 1590, 2300, 2400, 3000, 3300)
SUCCESS_TRANSITION_MS = 4000
SUCCESS_FRAME_INTERVAL_MS = 250
OVERLAY = """() => {
    const style = document.createElement('style');
    style.textContent = `
        #dockingDemo { position: fixed; inset: 0; z-index: 2147483647;
            pointer-events: none; color: #f6fbff; font-family: Arial, sans-serif; }
        #dockingDemo .card { position: absolute; top: 24px; left: 24px;
            width: 300px; padding: 12px 14px; border: 1px solid #72e1ff66;
            border-radius: 10px; background: #07131edc;
            box-shadow: 0 14px 45px #0009; }
        #dockingDemo .title { margin: 0 0 10px; font-size: 18px;
            font-weight: 800; line-height: 1; }
        #dockingDemo .title span { margin-left: 8px; color: #75dfff;
            font-size: 9px; font-weight: 700; letter-spacing: 1px; }
        #dockingDemo .stats { display: flex; gap: 16px; }
        #dockingDemo .label { color: #a7bdca; font-size: 9px;
            letter-spacing: 1px; text-transform: uppercase; }
        #dockingDemo .value { margin-top: 2px; font-size: 14px; font-weight: 700; }
        #dockingDemo .phase { color: #75eec7; }
        #dockingDemo .brake { margin-top: 8px; padding-top: 7px;
            border-top: 1px solid #75dfff33; color: #a7bdca; font-size: 9px;
            letter-spacing: .7px; }
        #dockingDemo .brake strong { margin-left: 6px; color: #75eec7; }
    `;
    document.head.appendChild(style);
    const overlay = document.createElement('div');
    overlay.id = 'dockingDemo';
    overlay.innerHTML = `
        <div class="card">
            <div class="title">ISS Docking<span>JEV GUIDANCE</span></div>
            <div class="stats">
                <div><div class="label">Jev phase</div><div class="value phase" id="demoPhase">ALIGN</div></div>
                <div><div class="label">Range</div><div class="value" id="demoRange">—</div></div>
                <div><div class="label">Decision</div><div class="value" id="demoDecision">1</div></div>
            </div>
            <div class="brake" id="demoBrakeRow">BRAKING<strong id="demoBrake">PENDING</strong></div>
        </div>
    `;
    document.body.appendChild(overlay);
}"""
DRAW_ON_DEMAND = """() => {
    const drawScene = renderer.render.bind(renderer);
    const drawNavball = navballRenderer.render.bind(navballRenderer);
    let sceneArgs;
    let navballArgs;
    renderer.render = (...args) => { sceneArgs = args; };
    navballRenderer.render = (...args) => { navballArgs = args; };
    window.__demoDrawFrame = () => {
        if (sceneArgs) drawScene(...sceneArgs);
        if (navballArgs) drawNavball(...navballArgs);
    };
}"""


def advance_to_frame(page, target: int) -> int:
    while True:
        frame = page.evaluate("window.__jevFrame")
        if frame >= target:
            return frame
        remaining = target - frame
        page.clock.run_for(1000 if remaining > 65 else (100 if remaining > 7 else 17))


def update_overlay(page, frame: int, decisions: list[dict]) -> None:
    latest = next(
        (index for index in range(len(decisions) - 1, -1, -1)
         if decisions[index]["frame"] <= frame),
        0,
    )
    braking_choices = [
        decision["braking"]["choice"] for decision in decisions
        if decision["frame"] <= frame and "braking" in decision
    ]
    braking_status = (
        "DOCKING SPEED" if braking_choices[-1] != "continue_slow" else "KEEP CLOSING"
    ) if braking_choices else "PENDING"
    state = page.evaluate(READ_STATE)
    page.evaluate(
        """data => {
            document.querySelector('#demoPhase').textContent = data.phase;
            document.querySelector('#demoRange').textContent = data.range;
            document.querySelector('#demoDecision').textContent = data.decision;
            document.querySelector('#demoBrake').textContent = data.braking;
            document.querySelector('#demoBrakeRow').style.display = data.showBraking ? '' : 'none';
        }""",
        {
            "phase": decisions[latest]["applied_phase"].upper(),
            "range": f"{state['range_m']:.1f} m",
            "decision": str(latest + 1),
            "braking": braking_status,
            "showBraking": bool(braking_choices),
        },
    )


def render(trace_path: Path, output: Path, preview: bool, fps: int, stride: int) -> None:
    data = json.loads(trace_path.read_text())
    if data["outcome"] != "SUCCESS" or not data.get("result"):
        raise ValueError("Demo requires a recorded successful docking")
    output.mkdir(parents=True, exist_ok=True)
    target_frame = data["final"]["frame"]
    final_visual_frame = target_frame - 1
    targets = (
        sorted({frame for frame in PREVIEW_FRAMES if frame < final_visual_frame}
               | {final_visual_frame})
        if preview else list(range(START_FRAME, final_visual_frame, stride))
        + [final_visual_frame]
    )
    events = [{"frame": record["frame"], "actions": record["actions"]}
              for record in data["records"]]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        try:
            page.clock.install()
            open_simulator(page)
            begin_episode_fast(page)
            page.clock.pause_at(time.time() + 10)
            page.evaluate(INSTALL_REPLAY, events)
            apply_scenario(page, Scenario(**data["scenario"]))
            page.evaluate(DRAW_ON_DEMAND)
            page.evaluate(OVERLAY)
            page.evaluate("window.__jevFrame = 0; window.__replayArmed = true")

            for index, target in enumerate(targets):
                frame = advance_to_frame(page, target)
                update_overlay(page, frame, data["decisions"])
                page.evaluate("window.__demoDrawFrame()")
                name = f"preview_{frame:04d}.png" if preview else f"frame_{index:04d}.png"
                page.screenshot(path=str(output / name))
                if index % 25 == 0 or index == len(targets) - 1:
                    print(f"captured={index + 1}/{len(targets)} simulator_frame={frame}", flush=True)

            advance_to_frame(page, target_frame)
            replay_result = page.evaluate("window.__replayResult")
            difference = (
                max_capture_difference(replay_result["preTelemetry"], data["result"]["telemetry"])
                if replay_result else math.inf
            )
            if (replay_result is None or replay_result["outcome"] != "SUCCESS"
                    or not math.isclose(difference, 0, abs_tol=1e-3)):
                raise ValueError(f"Demo replay diverged from recorded success: {difference}")
            print(f"replay_match=True max_capture_difference={difference:.6f}", flush=True)

            page.evaluate("document.querySelector('#dockingDemo').remove()")
            transition_steps = range(0, SUCCESS_TRANSITION_MS + 1,
                                     SUCCESS_FRAME_INTERVAL_MS)
            for index, elapsed_ms in enumerate(transition_steps):
                if index:
                    page.clock.run_for(SUCCESS_FRAME_INTERVAL_MS)
                page.evaluate("window.__demoDrawFrame()")
                if not preview or elapsed_ms == SUCCESS_TRANSITION_MS:
                    name = ("preview_success.png" if preview
                            else f"frame_{len(targets) + index:04d}.png")
                    page.screenshot(path=str(output / name))
            if not page.get_by_text("SUCCESS", exact=True).is_visible():
                raise ValueError("Simulator success screen did not appear")
            print("success_screen_captured=True", flush=True)
        finally:
            browser.close()

    if preview:
        return
    video_path = output / "docking.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
         "-i", str(output / "frame_%04d.png"), "-vf", "tpad=stop_mode=clone:stop_duration=2",
         "-c:v", "libx264", "-crf", "23",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)],
        check=True,
    )
    for pattern in ("frame_*.png", "frame_*.jpg"):
        for frame_path in output.glob(pattern):
            frame_path.unlink()
    print(f"video={video_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview", action="store_true", help="render review stills")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--stride", type=int, default=14)
    args = parser.parse_args()
    render(args.trace, args.output, args.preview, args.fps, args.stride)


if __name__ == "__main__":
    main()
