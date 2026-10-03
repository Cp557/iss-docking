"""Launch the SpaceX ISS Docking Simulator and get it into a running episode."""

from playwright.sync_api import Page

SIMULATOR_URL = "https://iss-sim.spacex.com/"

BEGIN_BUTTON_DELAY_MS = 2000
WARP_TRANSITION_TIMEOUT_MS = 120000


def open_simulator(page: Page) -> None:
    page.goto(SIMULATOR_URL)


def begin_episode(page: Page) -> None:
    """Click BEGIN and wait for the warp transition into the docking scene to finish."""
    begin_button = page.get_by_text("BEGIN", exact=True)
    begin_button.wait_for()
    page.wait_for_timeout(BEGIN_BUTTON_DELAY_MS)
    begin_button.click()
    page.wait_for_function("() => window.isGameOver === false", timeout=WARP_TRANSITION_TIMEOUT_MS)


def begin_episode_fast(page: Page) -> None:
    """Complete the simulator's cosmetic warp using its own animation endpoints."""
    begin_button = page.get_by_text("BEGIN", exact=True)
    begin_button.wait_for()
    begin_button.click()
    page.evaluate(
        """() => {
            if (!isWarpComplete) finishWarp();
            gsap.getTweensOf(issObject.position).forEach(tween => tween.progress(1));
            interfaceAnimationIn.progress(1);
            warpVars.isFinished = true;
            warpVars.isTriggered = false;
        }"""
    )
    page.wait_for_function("() => window.isGameOver === false")


def disable_gpu_rendering(page: Page) -> None:
    """Skip expensive draw calls while preserving physics and collision checks."""
    page.evaluate(
        """() => {
            renderer.render = () => {};
            navballRenderer.render = () => {};
        }"""
    )
