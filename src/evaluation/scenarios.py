"""Seeded starting states for repeatable simulator evaluation."""

import random
from dataclasses import asdict, dataclass

from playwright.sync_api import Page


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int | None
    x_error: float
    y_error: float
    z_error: float
    roll_deg: float
    pitch_deg: float
    yaw_deg: float

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_SCENARIO = Scenario(
    name="default",
    seed=None,
    x_error=200.0,
    y_error=12.0,
    z_error=30.0,
    roll_deg=15.0,
    pitch_deg=-20.0,
    yaw_deg=-10.0,
)

NEAR_DOCKING_SCENARIO = Scenario(
    name="near_docking",
    seed=None,
    x_error=8.0,
    y_error=0.0,
    z_error=0.0,
    roll_deg=0.0,
    pitch_deg=0.0,
    yaw_deg=0.0,
)


def random_scenario(seed: int) -> Scenario:
    """Generate a safe, at-rest start with randomized position and attitude."""
    rng = random.Random(seed)
    return Scenario(
        name="random",
        seed=seed,
        x_error=rng.uniform(120.0, 250.0),
        y_error=rng.uniform(-30.0, 30.0),
        z_error=rng.uniform(-30.0, 30.0),
        roll_deg=rng.uniform(-25.0, 25.0),
        pitch_deg=rng.uniform(-25.0, 25.0),
        yaw_deg=rng.uniform(-25.0, 25.0),
    )


def random_near_docking_scenario(seed: int) -> Scenario:
    """Vary a settled start near the port without requiring lateral control."""
    rng = random.Random(seed)
    return Scenario(
        name="near_docking",
        seed=seed,
        x_error=rng.uniform(6.0, 10.0),
        y_error=rng.uniform(-0.04, 0.04),
        z_error=rng.uniform(-0.04, 0.04),
        roll_deg=rng.uniform(-0.1, 0.1),
        pitch_deg=rng.uniform(-0.1, 0.1),
        yaw_deg=rng.uniform(-0.1, 0.1),
    )


def apply_scenario(
    page: Page, scenario: Scenario, arm_pd: bool = False, arm_jev: bool = False
) -> None:
    """Atomically reset a scenario and optionally arm an in-page controller."""
    page.evaluate(
        """({scenario, armPd, armJev}) => {
            const toRad = Math.PI / 180;

            camera.position.set(
                issObject.position.x + scenario.y_error,
                issObject.position.y + scenario.z_error,
                issObject.position.z + scenario.x_error,
            );
            camera.rotation.set(
                scenario.pitch_deg * toRad,
                scenario.yaw_deg * toRad,
                scenario.roll_deg * toRad,
                camera.rotation.order,
            );
            camera.updateMatrixWorld(true);

            motionVector.set(0, 0, 0);
            currentRotationX = 0;
            currentRotationY = 0;
            currentRotationZ = 0;
            targetRotationX = 0;
            targetRotationY = 0;
            targetRotationZ = 0;
            rateRotationX = 0;
            rateRotationY = 0;
            rateRotationZ = 0;

            prevRange = camera.position.distanceTo(issObject.position);
            smoothRangeRate = 0;
            rateCurrent = 0;
            rangeRateCounter = 0;
            prevRangeTime = Date.now();

            if (armPd) {
                window.__pdStats.scenario = scenario;
                window.__pdControllerArmed = true;
            }
            if (armJev) {
                window.__jevServo.scenario = scenario;
                window.__jevServoArmed = true;
            }
        }""",
        {"scenario": scenario.as_dict(), "armPd": arm_pd, "armJev": arm_jev},
    )


def apply_scenario_and_arm(page: Page, scenario: Scenario) -> None:
    """Reset a scenario and release the installed PD controller."""
    apply_scenario(page, scenario, arm_pd=True)
