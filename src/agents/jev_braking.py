"""Projected final-approach state and safety checks shared by Jev experiments."""

import math


DEADLINE_RESERVE_FRAMES = 100
BRAKING_ROOM_MARGIN_M = 0.05
TARGET_SPEED = 0.0025
DECELERATION_PER_FRAME = 0.001

ARRIVAL_QUESTION = {
    "switch_viable": {
        "type": "noul",
        "instructions": (
            "Would switching the target velocity from -0.02 to -0.0025 m/frame "
            "now allow safe docking before frame 1500? A yes requires both enough "
            "distance to reduce speed below 0.004 m/frame before the 0.2 m "
            "docking boundary and an estimated arrival frame at or before 1500. "
            "Use the supplied estimated arrival frame rather than assuming that "
            "a slower speed is always safer."
        ),
    }
}

CAPTURE_ARRIVAL_QUESTION = {
    "switch_viable": {
        "type": "noul",
        "instructions": (
            "During the current final approach, should the target velocity switch "
            "from -0.02 to -0.0025 m/frame now? A yes requires enough room to "
            "reduce speed below 0.004 m/frame before the 0.2 m docking boundary "
            "and an estimated arrival at or before the supplied deadline_frame. "
            "Use the supplied estimated arrival frame. A no means continue "
            "closing at -0.02 for a few more physics frames."
        ),
    }
}


def projected_state(state: dict, deadline_frame: int = 1500) -> dict:
    distance = state["distance_m"]
    velocity = abs(state["velocity_m_per_frame"])
    braking_frames = max(0, (velocity - TARGET_SPEED) / DECELERATION_PER_FRAME)
    braking_distance = max(
        0, (velocity**2 - TARGET_SPEED**2) / (2 * DECELERATION_PER_FRAME)
    )
    remaining_at_creep = max(0, distance - braking_distance - 0.2)
    arrival_frame = (
        state["elapsed_frames"] + braking_frames + remaining_at_creep / TARGET_SPEED
    )
    return {
        "approach_distance_m": round(distance, 4),
        "approach_velocity_m_per_frame": round(state["velocity_m_per_frame"], 6),
        "elapsed_frame": state["elapsed_frames"],
        "deadline_frame": deadline_frame,
        "distance_to_docking_boundary_m": round(distance - 0.2, 4),
        "estimated_braking_distance_m": round(braking_distance, 4),
        "estimated_arrival_frame_if_switch_now": round(arrival_frame),
        "target_velocity_after_switch_m_per_frame": -TARGET_SPEED,
    }


def guarded_brake_decision(
    state: dict, probability: float, step_duration_ms: int
) -> tuple[bool, str, str]:
    """Accept Jev's switch with time margin; brake before room runs out."""
    braking_room = (
        state["distance_to_docking_boundary_m"]
        - state["estimated_braking_distance_m"]
    )
    if braking_room <= 0:
        return True, "emergency_brake", "insufficient_braking_room"

    next_frames = math.ceil(step_duration_ms / 16)
    closing_speed = max(0, -state["approach_velocity_m_per_frame"])
    if braking_room <= closing_speed * next_frames + BRAKING_ROOM_MARGIN_M:
        return True, "switch_to_creep", "last_safe_braking_point"

    if (probability >= 0.5 and
            state["estimated_arrival_frame_if_switch_now"] <=
            state["deadline_frame"] - DEADLINE_RESERVE_FRAMES):
        return True, "switch_to_creep", "jev"

    reason = "deadline_reserve" if probability >= 0.5 else "jev_wait"
    return False, "continue_slow", reason
