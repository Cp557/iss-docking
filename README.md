# Jev-Guided ISS Docking

Jev guides a spacecraft through the [SpaceX ISS Docking Simulator](https://iss-sim.spacex.com/). It chooses flight phases and when to brake; a controller handles individual thruster pulses. The simulator pauses while Jev makes each decision.

## Demo

https://github.com/user-attachments/assets/0d257062-8974-40f1-ab3f-77e8f9bae471

This is a replay of a successful docking from the simulator's normal start. It shows 156 Jev phase decisions and nine braking judgments. [Download the MP4](demo/docking.mp4) · [View the recording](demo/recording.json)

## Results

- **10/10 full dockings** from randomized starts 135–224 m away.
- **20/20 final approaches** from aligned starts 6–10 m away.

Jev made the high-level choices; the controller stabilized the craft and a safety guard checked braking. These are small simulator trials. The full-distance controller brings different starts to similar final-approach states.

[Full-docking results](demo/full-distance-results.json) · [Braking results](demo/braking-results.json) · [Technical reference](REFERENCE.md)

## Run it

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m scripts.replay --headed --fast-start
```

Replay needs no API key. To make new Jev decisions, set `JEV_API_KEY` in `.env` or the environment, then run `.venv/bin/python -m scripts.dock`. Add `--seed N` for a repeatable randomized start.

This independent experiment uses a third-party browser simulator, not spacecraft flight software.
