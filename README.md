# Jev-Guided ISS Docking

## Demo

[Watch the 33-second docking demo](demo/docking.mp4)

[Download the MP4](demo/docking.mp4) · [Technical reference](REFERENCE.md)

A Jev-guided controller for the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/). Jev reads simulator telemetry and chooses the flight phase and when to slow for docking. A frame-level controller turns those choices into thruster pulses. Playwright pauses the simulator while Jev responds, so API latency does not move the craft.

The demo starts at the simulator's normal 202.6 m position and replays a successful run: **156 phase decisions, nine braking judgments, and a simulator-declared docking**. The panel in the upper left labels the recorded Jev decisions; it is added to the video and is not part of the simulator. The video uses saved actions, so it does not call Jev during playback.

## Results

| Trial | Jev's role | Result |
| --- | --- | --- |
| Full docking from five randomized starts, 153–224 m away | Choose phases and final braking point | **5/5 docked** |
| Final approach from twenty aligned starts, 6–10 m away | Choose braking point | **20/20 docked** |

[Full-distance results](demo/full-distance-results.json) · [Final-approach results](demo/braking-results.json) · [Recorded demo run](demo/recording.json)

Jev made the high-level decisions. The deterministic controller stabilized attitude, tracked target speed, and issued each thruster pulse. Readiness checks gated phase changes; a 100-frame arrival reserve and braking-room check guarded the speed switch. In the five full-distance trials, Jev selected every braking switch without a guard override. In the twenty near-port trials, the deadline reserve deferred 14 proposals before Jev selected the accepted switches.

These are browser-simulator results. The phase controller brings varied full-distance starts to similar 8 m capture states, so the full-distance trials do not test a wide range of braking conditions.

## Run

Install Python dependencies and Chromium:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

Replay the recorded docking in a visible browser without an API key:

```bash
.venv/bin/python -m scripts.replay --headed --fast-start
```

To make new Jev decisions, set `JEV_API_KEY` in `.env` or the environment:

```bash
.venv/bin/python -m scripts.dock
.venv/bin/python -m scripts.dock --seed 5
.venv/bin/python -m scripts.evaluate_braking --seed 15
```

Runs write decision and action traces to the ignored `results/` directory. To rebuild the demo video from the included recording, install `ffmpeg` and run:

```bash
.venv/bin/python -m scripts.render_demo
```

The simulator is a third-party site, so new runs depend on its current code and availability. This is an independent experiment, not a spacecraft flight system or a SpaceX project.
