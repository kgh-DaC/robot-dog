void initFailsafe () {
  
}

void updateFailsafe() {
  bool sampleValid;
  unsigned long sampleAt;
  portENTER_CRITICAL(&telemetryMux);
  sampleValid = imuSampleValid;
  sampleAt = imuSampleAtMicros;
  portEXIT_CRITICAL(&telemetryMux);

  bool armed;
  uint32_t lastMoveAt;
  portENTER_CRITICAL(&movementMux);
  armed = movementArmed;
  lastMoveAt = lastValidMoveAtMicros;
  portEXIT_CRITICAL(&movementMux);

  if (!armed) {
    FS_FAIL = true;
    return;
  }

  const uint32_t commandTimeoutUs = (uint32_t)FS_WS_THR * (uint32_t)LOOP_TIME;
  bool commandTimedOut =
      (uint32_t)(micros() - lastMoveAt) >= commandTimeoutUs;
  bool imuFault =
      (!imuReady || !sampleValid ||
       (unsigned long)(micros() - sampleAt) > IMU_TELEMETRY_STALE_US);
  if (commandTimedOut || imuFault) {
    setFailsafe();
    return;
  }
  
  FS_FAIL = false;
}

bool queueMovementCommand(const moveVector &command) {
  bool accepted = false;
  portENTER_CRITICAL(&movementMux);
  if (movementArmed) {
    pendingMovement = command;
    pendingMovementUpdate = true;
    lastValidMoveAtMicros = micros();
    accepted = true;
  }
  portEXIT_CRITICAL(&movementMux);
  return accepted;
}

void applyPendingMovement() {
  moveVector command;
  bool hasUpdate = false;
  portENTER_CRITICAL(&movementMux);
  if (pendingMovementUpdate) {
    command = pendingMovement;
    pendingMovementUpdate = false;
    hasUpdate = true;
  }
  portEXIT_CRITICAL(&movementMux);
  if (hasUpdate) vector = command;
}

void setFailsafe() {
  portENTER_CRITICAL(&movementMux);
  movementArmed = false;
  pendingMovement = {{0, 0, 0}, {0, 0, 0}};
  pendingMovementUpdate = true;
  portEXIT_CRITICAL(&movementMux);
  FS_FAIL     = true;
}
