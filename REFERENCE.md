# Technical Reference

Canonical description of the current `iss-docking` code. Update this when the controller, simulator integration, or recorded evidence changes.

## System

The project automates the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/) with Playwright. It reads structured state from the page's JavaScript globals, pauses simulator time for each Jev API call, and runs an in-page actuator once per physics frame. Jev chooses mission phases and one final-approach speed change. The actuator handles attitude, lateral correction, target-speed tracking, and individual thruster pulses.

```text
Simulator state → Jev phase/braking decision → readiness and braking checks
                                                   ↓
Simulator controls ← in-page frame controller ← allowed target
```

The actuator is installed in the page's `render()` loop in `src/agents/jev_servo.js`. It records every frame's control actions and the simulator's result. The controller uses `camera.quaternion` to convert world-axis target velocity into the camera's local control frame. Translation pulses change `motionVector` by roughly 0.001 per frame; velocity persists between pulses. Rotation controls change target angles by 0.5° and the simulator eases angular rate toward those targets.

The simulator declares success below 0.2 m range when each attitude error is at most 0.2° and each raw `motionVector` component is at most 0.004. Its raycast can declare collision before reaching the docking boundary. These are simulator rules, not real spacecraft constraints.

## Starting states

`src/evaluation/scenarios.py` defines the fixed and seeded scenarios. The default state reproduces the simulator's native start: 200 m approach error, 12 m and 30 m lateral errors, and roll/pitch/yaw of 15°/−20°/−10°. True range is about 202.6 m. Random full-distance starts sample 120–250 m approach error, ±30 m lateral errors, and ±25° attitude errors. All start at rest.

The near-port experiment starts aligned at 8 m. Its seeded starts vary range from 6–10 m, lateral offsets within ±0.04 m, and attitude errors within ±0.1°.

`apply_scenario()` resets the simulator after the BEGIN transition and arms the controller. The demo uses the default state, so it begins at the simulator's normal position. `begin_episode_fast()` finishes only the cosmetic warp animation before an episode; it does not change the physics or starting state.

## Jev decisions

`scripts/dock.py` sends Jev the current phase, telemetry, readiness flags, and the effects of each phase. The phase question offers `hold`, `align`, `lineup`, `cruise`, and `capture`:

- **Align:** stabilize attitude while holding range.
- **Line up:** remove lateral error while holding range.
- **Cruise:** close to an 8 m standoff.
- **Capture:** close the final 8 m.

Readiness gates prevent line up before attitude is stable, cruise before line up, and capture before the standoff is ready. Once capture starts, Jev chooses only between continuing it and holding for safety. The first capture step also asks a separate yes/no Jev question: whether switching the target from −0.02 to −0.0025 m/frame now can dock safely before the supplied deadline. Further questions continue until a switch is committed.

`src/agents/jev_braking.py` estimates braking distance and arrival frame. The full run sets its deadline 1,500 frames after capture begins; the isolated near-port run uses frame 1,500. A proposed switch needs 100 frames of arrival reserve. The guard also switches before available braking room is exhausted. An `emergency_brake` result records an already unsafe state; it does not guarantee recovery. Each decision and reason is saved.

The isolated near-port test is `scripts/evaluate_braking.py`. It uses the same arrival question and guard, but begins with settled attitude and a −0.02 m/frame approach target. The in-page controller remains responsible for tracking speed and attitude.

## Recording and replay

`src/recording.py` saves the scenario, Jev decisions, frame-indexed control actions, simulator result, and final state. Live runs write to ignored `results/` files. The included successful default-start run is `demo/recording.json`.

`scripts/replay.py` applies the saved actions at their original frame numbers. It verifies the simulator's declared outcome and compares the game-over telemetry against the recording, allowing at most 0.001 numeric difference. It needs simulator access but no Jev key. `scripts/render_demo.py` samples this replay from frame 0, adds a labeled decision panel, checks the same game-over match, then captures the simulator's success transition and holds its success screen at the end of `demo/docking.mp4`. The panel is a video overlay, not a simulator control.

## Evidence and limits

- The default-start demo run docked at frame 4,269 after 156 Jev phase decisions and nine braking judgments. Jev selected the braking switch at frame 3,125. Its packaged action replay matched the recorded game-over state exactly.
- Ten full-distance seeds, 5–14, docked 10/10. True starting ranges were 134.75–223.97 m. Jev made 131–154 phase decisions and nine braking judgments per run. It selected all accepted braking switches near 3.22 m; no readiness vetoes or braking-guard overrides were needed. Seeds 10–14 were run after the first five were reported. Summary: `demo/full-distance-results.json`.
- Twenty previously unused near-port seeds, 15–34, docked 20/20. Jev made each accepted switch; the deadline reserve deferred 14 earlier proposals. Summary: `demo/braking-results.json`.

The full-distance controller converges different starts to nearly identical 8 m capture states. The twenty near-port runs test a wider range of braking states, but begin aligned and at rest. These samples measure the browser simulator only. Jev does not infer physics from images or choose every thruster pulse.

## Jev control-limit experiments

`scripts/experiment_jev_control.py` runs separately from the guarded demo and writes full traces to ignored `results/` files. Its `phase` mode makes **brake** a Jev-selected phase instead of asking a second braking question. The existing readiness gates still protect phase transitions, but no automatic braking override changes Jev's choice. The actuator targets −0.02 m/frame during capture and −0.0025 m/frame during brake.

The `velocity`, `velocity_paced`, and `velocity_instructed` modes ask Jev for approach and two lateral velocity targets in a single API request. The actuator still settles attitude and tracks those velocity targets with frame-level pulses; there is no scripted phase trajectory, readiness veto, or braking override. `velocity_paced` adds a frame-5,000 goal and travel-time estimates. `velocity_instructed` also states alignment and braking rules in the prompt and supplies braking room. These modes test structured-state decisions, not image perception or raw thruster selection.

On the default start and unused seeds 15–16, brake-as-phase docked 3/3, but Jev switched near 7.6 m every time and missed the capture deadline by about 1,427–1,428 frames. On the default start, unpaced velocity control timed out after 250 decisions, paced control collided near the port with about 0.79 m of lateral error, and instructed control timed out after 250 decisions without starting the long approach. The three velocity trials are different prompts on one start, so they do not establish a general success rate. Summary: `demo/jev-control-results.json`.

## Current files

```text
README.md                      Overview and commands
REFERENCE.md                   This technical reference
scripts/dock.py                Full Jev-guided docking run
scripts/experiment_jev_control.py  Separate phase and velocity experiments
scripts/evaluate_braking.py    Isolated guarded braking run
scripts/replay.py              Exact action replay
scripts/render_demo.py         Demo video renderer
src/agents/jev_client.py       Jev HTTP client and .env loading
src/agents/jev_braking.py      Arrival estimate, question, and guard
src/agents/jev_servo.js        Per-frame in-page controller
src/browser/simulator.py       Simulator startup helpers
src/evaluation/scenarios.py    Fixed and seeded starts
src/recording.py               Decision and action trace writer
src/telemetry/state.py         Page-state reader
demo/                         Video, cover image, recording, result summaries
results/                      Generated runs, ignored by Git
```
