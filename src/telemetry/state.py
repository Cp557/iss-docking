"""Simulator state used by Jev decisions and replay verification."""

READ_STATE = """() => {
    const deg = 180 / Math.PI;
    return {
        frame: window.__jevFrame || 0,
        game_over: isGameOver,
        fail_message: document.querySelector('#fail-message')?.textContent.trim() || '',
        range_m: camera.position.distanceTo(issObject.position),
        position_error_m: {
            approach: camera.position.z - issObject.position.z,
            lateral_y: camera.position.x - issObject.position.x,
            lateral_z: camera.position.y - issObject.position.y,
        },
        velocity_m_per_frame: {
            approach: motionVector.z,
            lateral_y: motionVector.x,
            lateral_z: motionVector.y,
        },
        angle_deg: {
            roll: camera.rotation.z * deg,
            pitch: camera.rotation.x * deg,
            yaw: camera.rotation.y * deg,
        },
        angular_rate_deg_per_frame: {
            roll: currentRotationZ * deg,
            pitch: currentRotationX * deg,
            yaw: currentRotationY * deg,
        },
        rotation_target_deg: {
            roll: targetRotationZ * deg,
            pitch: targetRotationX * deg,
            yaw: targetRotationY * deg,
        },
    };
}"""
