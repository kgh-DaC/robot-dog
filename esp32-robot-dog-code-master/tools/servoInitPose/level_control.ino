// Body leveling (stage 2/3): keep the body level while standing armed or walking.
//
// Input : body roll / pitch from imu_sensor.ino (needs IMUMAP; IMUZERO advised).
//         roll > 0 = right side lower, pitch > 0 = nose lower.
// Output: a per-leg GAMMA offset added on top of the commanded pose
//         (levelOffsetDeg[], used by pulseForServo). The walking pose itself is
//         unchanged, so STATUS/angles still report the commanded base angles.
//
// Control: slow integral action with a dead band. The compensation (deg of
// leg "extension") grows while the body is tilted and holds when level:
//   rollComp  += LEVEL_RATE_PER_S * roll  * dt   (extend right legs if right low)
//   pitchComp += LEVEL_RATE_PER_S * pitch * dt   (extend front legs if nose low)
//   leg extension = side * rollComp + front * pitchComp, clamped to +-LEVEL_MAX_COMP_DEG
// The rate is slow on purpose: the accelerometer is disturbed while walking and
// the open-loop gait swaps diagonal legs every 1.2 s.
//
// MEASURED (2026-11-11, robot standing on the floor, not touched, leveling off,
// SET on LF+LH GAMMA, 1.5 s per step): 90/84/90/96/90 -> roll -6.1/-9.0/-6.5/
// -3.9/-5.5 deg. A HIGHER logical GAMMA angle supports that side higher
// (= leg extension), about 0.45 deg of roll per deg of GAMMA, so sign +1.
// The max compensation (6 deg GAMMA) therefore corrects only ~2.7 deg of tilt.
// With the robot lifted the knee looked "longer" at a lower angle; that visual
// cue is misleading. The first floor test with sign -1 made the tilt worse
// (roll -5.8 -> -8.9) and was stopped by level_diverge.
//
// Safety (all in loop(), independent of the web page):
// - Leveling is OFF after every boot and after every STOP/DISARM. LEVEL ON is
//   accepted only from the armed controller and needs a calibrated axis map.
// - While armed: tilt > LEVEL_TILT_LIMIT_DEG -> STOP (reason tilt_limit). Uses
//   roll/pitch when mapped, else the raw sensor X/Y angles; leveling on or off.
//   ARM is refused while the tilt is already beyond the limit.
// - While armed with leveling on: no valid sample for LEVEL_IMU_LOST_MS -> STOP
//   (reason imu_lost).
// - Divergence guard: a compensation axis saturated while that tilt is
//   LEVEL_DIVERGE_DEG worse than when leveling started, for LEVEL_DIVERGE_MS
//   -> STOP (reason level_diverge). Catches a wrong IMUMAP or GAMMA sign.
// - Every STOP/DISARM clears the compensation (legs return to the plain pose).
// - Not covered: with leveling off, invalid samples (shaking, impacts) do not
//   stop the robot; the tilt check simply has no data while they last.

const float LEVEL_RATE_PER_S = 0.6f;         // deg of extension per deg of tilt per second
const float LEVEL_DEADBAND_DEG = 1.0f;       // ignore tilt smaller than this
const float LEVEL_MAX_COMP_DEG = 6.0f;       // max GAMMA offset per leg
const float LEVEL_TILT_LIMIT_DEG = 20.0f;    // stop beyond this tilt (user default, not measured)
const uint16_t LEVEL_IMU_LOST_MS = 1000;
const float LEVEL_DIVERGE_DEG = 2.0f;        // tilt worse than at start by this much ...
const uint16_t LEVEL_DIVERGE_MS = 1000;      // ... with saturated compensation, this long
const uint16_t LEVEL_UPDATE_MS = 20;
const float LEVEL_GAMMA_EXTEND_SIGN = 1.0f;  // extension -> GAMMA change sign (see MEASURED)
const int8_t LEVEL_LEG_SIDE[4] = {-1, +1, -1, +1};   // LF RF LH RH: right = +1
const int8_t LEVEL_LEG_FRONT[4] = {+1, +1, -1, -1};  // front = +1

bool levelEnabled = false;
float levelRollComp = 0, levelPitchComp = 0;
unsigned long levelLastUpdate = 0;
unsigned long levelImuOkAt = 0;
bool levelBaselinePending = true;            // capture start tilt at the first active update
float levelStartRoll = 0, levelStartPitch = 0;
unsigned long levelDivergeSince = 0;

bool levelSensorUsable()
{
  return imuValid() && imuMapped();
}

bool levelActive()
{
  return levelEnabled && armed && levelSensorUsable();
}

float levelClamp(float value)
{
  return value > LEVEL_MAX_COMP_DEG ? LEVEL_MAX_COMP_DEG : value < -LEVEL_MAX_COMP_DEG ? -LEVEL_MAX_COMP_DEG : value;
}

float levelLegExtension(int leg)
{
  return levelClamp(LEVEL_LEG_SIDE[leg] * levelRollComp + LEVEL_LEG_FRONT[leg] * levelPitchComp);
}

void levelApplyOffsets()
{
  for (int leg = 0; leg < 4; leg++) {
    const int index = leg * 3 + 2;  // GAMMA
    levelOffsetDeg[index] = LEVEL_GAMMA_EXTEND_SIGN * levelLegExtension(leg);
    targetPulses[index] = pulseForServo(index);
  }
}

// Legs go back to the plain (uncompensated) pose.
void levelReset()
{
  levelRollComp = levelPitchComp = 0;
  levelLastUpdate = 0;
  levelBaselinePending = true;
  levelDivergeSince = 0;
  levelApplyOffsets();
}

// Called by controlStop(): leveling must be switched on again after the next ARM.
void levelOnStop()
{
  levelEnabled = false;
  levelReset();
}

// Largest tilt in degrees: body roll/pitch when mapped, else raw sensor X/Y.
// NAN when there is no valid sample.
float levelTiltNow()
{
  if (!imuValid()) return NAN;
  if (imuMapped()) return fmaxf(fabsf(imuRoll()), fabsf(imuPitch()));
  return fmaxf(fabsf(imuAxisAngle(0)), fabsf(imuAxisAngle(1)));
}

// Called when a controller arms: start the "sensor lost" timer fresh.
void levelOnArm()
{
  levelImuOkAt = millis();
  levelLastUpdate = 0;
}

float levelDeadband(float value)
{
  if (value > LEVEL_DEADBAND_DEG) return value - LEVEL_DEADBAND_DEG;
  if (value < -LEVEL_DEADBAND_DEG) return value + LEVEL_DEADBAND_DEG;
  return 0;
}

// Called from loop() with controlMutex held, after imuUpdate().
void levelUpdate()
{
  const unsigned long now = millis();
  if (imuValid()) levelImuOkAt = now;

  const float tilt = levelTiltNow();
  if (armed && isfinite(tilt) && tilt > LEVEL_TILT_LIMIT_DEG) {
    controlStop("tilt_limit");
    Serial.println("EVENT STOPPED tilt_limit");
    return;
  }
  if (armed && levelEnabled && now - levelImuOkAt > LEVEL_IMU_LOST_MS) {
    controlStop("imu_lost");
    Serial.println("EVENT STOPPED imu_lost");
    return;
  }

  if (!levelEnabled) {
    if (levelRollComp != 0 || levelPitchComp != 0) levelReset();
    return;
  }
  if (!levelActive()) {  // disarmed, or a short sensor gap: hold, do not integrate
    levelLastUpdate = 0;
    return;
  }
  if (levelLastUpdate == 0) {
    levelLastUpdate = now;
    return;
  }
  if (now - levelLastUpdate < LEVEL_UPDATE_MS) return;
  float dt = (now - levelLastUpdate) / 1000.0f;
  if (dt > 0.1f) dt = 0.1f;
  levelLastUpdate = now;

  const float roll = imuRoll(), pitch = imuPitch();
  if (levelBaselinePending) {
    levelStartRoll = fabsf(roll);
    levelStartPitch = fabsf(pitch);
    levelBaselinePending = false;
  }
  const float saturated = LEVEL_MAX_COMP_DEG - 0.01f;
  const bool diverging =
      (fabsf(levelRollComp) >= saturated && fabsf(roll) > levelStartRoll + LEVEL_DIVERGE_DEG) ||
      (fabsf(levelPitchComp) >= saturated && fabsf(pitch) > levelStartPitch + LEVEL_DIVERGE_DEG);
  if (!diverging) {
    levelDivergeSince = 0;
  } else if (levelDivergeSince == 0) {
    levelDivergeSince = now;
  } else if (now - levelDivergeSince >= LEVEL_DIVERGE_MS) {
    controlStop("level_diverge");
    Serial.println("EVENT STOPPED level_diverge");
    return;
  }

  levelRollComp = levelClamp(levelRollComp + LEVEL_RATE_PER_S * levelDeadband(roll) * dt);
  levelPitchComp = levelClamp(levelPitchComp + LEVEL_RATE_PER_S * levelDeadband(pitch) * dt);
  levelApplyOffsets();
}

// ARM is refused while the robot is already tilted beyond the stop limit.
const char *levelArmCheck()
{
  const float tilt = levelTiltNow();
  if (isfinite(tilt) && tilt > LEVEL_TILT_LIMIT_DEG) return "tilt_limit";
  return NULL;
}

// sourceOwnsControl: the caller is the armed controller (or nobody is armed).
// LEVEL ON needs an armed caller, so nobody can pre-enable leg motion for the
// next person who arms. LEVEL OFF is always allowed for the owner / when disarmed.
const char *levelSet(bool on, bool sourceOwnsControl)
{
  if (armed && !sourceOwnsControl) return "controlled_by_other";
  if (on && !armed) return "not_armed";
  if (on && !imuMapped()) return "imu_not_mapped";
  if (on && !imuValid()) return "imu_not_valid";
  levelEnabled = on;
  levelImuOkAt = millis();
  levelReset();
  return NULL;
}

void printLevel()
{
  // trim= is the fixed standing-pose GAMMA trim (LF/RF/LH/RH, deg) that the
  // firmware adds to every commanded GAMMA; the digital twin needs it to draw
  // the angle that is really sent to the servo. Appended last so older
  // gateways that do not know the field keep working.
  Serial.printf("LEVEL enabled=%d active=%d sensor=%d rollcomp=%.2f pitchcomp=%.2f "
                "offsets=%.2f/%.2f/%.2f/%.2f max=%.1f limit=%.1f trim=%.1f/%.1f/%.1f/%.1f\n",
                levelEnabled ? 1 : 0, levelActive() ? 1 : 0, levelSensorUsable() ? 1 : 0,
                levelRollComp, levelPitchComp, levelOffsetDeg[2], levelOffsetDeg[5],
                levelOffsetDeg[8], levelOffsetDeg[11], LEVEL_MAX_COMP_DEG, LEVEL_TILT_LIMIT_DEG,
                standGammaTrimDeg[0], standGammaTrimDeg[1], standGammaTrimDeg[2],
                standGammaTrimDeg[3]);
}

void formatLevelJson(char *out, size_t size)
{
  snprintf(out, size,
           "{\"enabled\":%s,\"active\":%s,\"sensorUsable\":%s,\"rollCompDeg\":%.2f,\"pitchCompDeg\":%.2f,"
           "\"gammaOffsetDeg\":{\"LF\":%.2f,\"RF\":%.2f,\"LH\":%.2f,\"RH\":%.2f},"
           "\"maxCompDeg\":%.1f,\"tiltLimitDeg\":%.1f}",
           levelEnabled ? "true" : "false", levelActive() ? "true" : "false",
           levelSensorUsable() ? "true" : "false", levelRollComp, levelPitchComp,
           levelOffsetDeg[2], levelOffsetDeg[5], levelOffsetDeg[8], levelOffsetDeg[11],
           LEVEL_MAX_COMP_DEG, LEVEL_TILT_LIMIT_DEG);
}
