// Frame-level actuator for phases or velocity targets chosen by Jev.
(function () {
    if (window.__jevServo) return;

    const DEG = 180 / Math.PI;
    const state = {
        frames: 0,
        mode: "phase",
        phase: "hold",
        captureApproachTarget: -0.02,
        targetVelocity: { approach: 0, lateral_y: 0, lateral_z: 0 },
        attitudeFrames: 0,
        lineupFrames: 0,
        standoffFrames: 0,
        ready: { attitude: false, lineup: false, standoff: false },
        telemetry: null,
        result: null,
        trace: [],
    };
    window.__jevServo = state;
    window.__jevServoArmed = false;

    function clamp(value, limit) {
        return Math.max(-limit, Math.min(limit, value));
    }

    function positionRate(error, limit) {
        return Math.abs(error) <= 0.025 ? 0 : clamp(-0.012 * error, limit);
    }

    function cruiseRate(error) {
        if (Math.abs(error) <= 0.025) return 0;
        return -Math.sign(error) * Math.min(0.2, Math.sqrt(0.0012 * Math.abs(error)));
    }

    function pulseRotation(error, target, positive, negative, actions) {
        const desiredRate = clamp(-0.02 * error, 0.001);
        const targetError = -desiredRate / 0.01 - target;
        if (targetError > Math.PI / 720) {
            positive[0]();
            actions.push(positive[1]);
        } else if (targetError < -Math.PI / 720) {
            negative[0]();
            actions.push(negative[1]);
        }
    }

    function pulseVelocity(error, decrease, increase, actions) {
        if (error > 0.00045) {
            decrease[0]();
            actions.push(decrease[1]);
        } else if (error < -0.00045) {
            increase[0]();
            actions.push(increase[1]);
        }
    }

    function controlStep() {
        if (isGameOver || !window.__jevServoArmed) return;

        const actions = [];
        const angles = [camera.rotation.z, camera.rotation.x, camera.rotation.y];
        const rates = [currentRotationZ, currentRotationX, currentRotationY];
        const targets = [targetRotationZ, targetRotationX, targetRotationY];
        const x = camera.position.z - issObject.position.z;
        const y = camera.position.x - issObject.position.x;
        const z = camera.position.y - issObject.position.y;
        const lateral = Math.hypot(y, z);
        const lateralRate = Math.hypot(motionVector.x, motionVector.y);
        const maxAngle = Math.max(...angles.map(angle => Math.abs(angle * DEG)));
        const maxRate = Math.max(...rates.map(Math.abs));

        const attitude = maxAngle <= 0.15 && maxRate <= 0.00001 &&
            targets.every(target => Math.abs(target) < Math.PI / 720);
        const lineup = attitude && lateral <= 0.08 && lateralRate <= 0.002;
        const standoff = lineup && Math.abs(x - 8) <= 0.08 &&
            Math.abs(motionVector.z) <= 0.002;
        state.attitudeFrames = attitude ? state.attitudeFrames + 1 : 0;
        state.lineupFrames = lineup ? state.lineupFrames + 1 : 0;
        state.standoffFrames = standoff ? state.standoffFrames + 1 : 0;
        state.ready = {
            attitude: state.attitudeFrames >= 30,
            lineup: state.lineupFrames >= 30,
            standoff: state.standoffFrames >= 45,
        };

        pulseRotation(angles[0], targets[0], [rollRight, "rollRight"],
            [rollLeft, "rollLeft"], actions);
        pulseRotation(angles[1], targets[1], [pitchDown, "pitchDown"],
            [pitchUp, "pitchUp"], actions);
        pulseRotation(angles[2], targets[2], [yawRight, "yawRight"],
            [yawLeft, "yawLeft"], actions);

        let worldTarget;
        if (state.mode === "velocity") {
            worldTarget = new THREE.Vector3(
                state.targetVelocity.lateral_y,
                state.targetVelocity.lateral_z + 0.0001,
                state.targetVelocity.approach,
            );
        } else {
            let xTarget = 0;
            if (state.phase === "cruise") xTarget = cruiseRate(x - 8);
            if (state.phase === "capture" && x > 0) xTarget = state.captureApproachTarget;
            const lateralLimit = state.phase === "capture" ? 0.0025 :
                (state.phase === "lineup" || state.phase === "cruise" ? 0.04 : 0);
            worldTarget = new THREE.Vector3(
                positionRate(y, lateralLimit),
                positionRate(z, lateralLimit) + 0.0001,
                xTarget,
            );
        }
        const inverse = camera.quaternion.clone();
        if (inverse.invert) inverse.invert();
        else inverse.inverse();
        const localTarget = worldTarget.applyQuaternion(inverse);
        const localCurrent = motionVector.clone().applyQuaternion(inverse);

        pulseVelocity(localCurrent.x - localTarget.x, [translateLeft, "translateLeft"],
            [translateRight, "translateRight"], actions);
        pulseVelocity(localCurrent.y - localTarget.y, [translateDown, "translateDown"],
            [translateUp, "translateUp"], actions);
        pulseVelocity(localCurrent.z - localTarget.z, [translateForward, "translateForward"],
            [translateBackward, "translateBackward"], actions);

        state.telemetry = {
            frame: state.frames,
            phase: state.phase,
            position_m: { approach: x, lateral_y: y, lateral_z: z },
            velocity_m_per_frame: {
                approach: motionVector.z,
                lateral_y: motionVector.x,
                lateral_z: motionVector.y,
            },
            attitude_deg: { roll: angles[0] * DEG, pitch: angles[1] * DEG, yaw: angles[2] * DEG },
            range_m: Math.hypot(x, y, z),
            lateral_m: lateral,
            ready: state.ready,
        };
        state.trace.push({ frame: state.frames, actions });
        state.frames += 1;
        window.__jevFrame = state.frames;
    }

    const originalRender = render;
    window.render = function () {
        const wasGameOver = isGameOver;
        controlStep();
        originalRender();
        if (!wasGameOver && isGameOver) {
            const message = document.querySelector("#fail-message").textContent.trim();
            state.result = {
                outcome: message ? "FAIL" : "SUCCESS",
                message,
                frame: state.frames,
                telemetry: state.telemetry,
            };
        }
    };
})();
