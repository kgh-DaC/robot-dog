int bytesToInt16(uint8_t byte1, uint8_t byte2) {
  return (byte1 << 8) | byte2;
}

bool pMove(uint8_t* data) {
  if (data[1] == 0) {
    setFailsafe();
    return false;
  }
  for (uint8_t offset = 2; offset < 14; offset += 2) {
    if (bytesToInt16(data[offset], data[offset + 1]) > 20000) {
      setFailsafe();
      return false;
    }
  }
  
  moveVector command = {
    {
      float(bytesToInt16(data[2], data[3])) / 10000 - 1,
      float(bytesToInt16(data[4], data[5])) / 10000 - 1,
      float(bytesToInt16(data[6], data[7])) / 10000 - 1
    },
    {
      float(bytesToInt16(data[10], data[11])) / 10000 - 1,
      float(bytesToInt16(data[8], data[9])) / 10000 - 1,
      float(bytesToInt16(data[12], data[13])) / 10000 - 1
    }
  };
  if (!queueMovementCommand(command)) {
    setFailsafe();
    return false;
  }
  return true;
}

void pTelemetry(uint8_t* telemetryPackage) {
  static_assert(LEG_NUM * 3 == P_TELEMETRY_JOINT_COUNT,
                "Telemetry v2 requires exactly 12 servo joint angles");

  /**
   * Telemetry package map
   * offset | type | length | description
   * -------|------|--------|-------------
   *      0 | char |      1 | 'T' (P_TELEMETRY)
   *      1 | uint |      1 | protocol version (2)
   *      2 | bin  |      2 | sensor state flags; bit 4 is software armed state
   *      4 | int  |      6 | IMU X/Y/Z in centidegrees
   *     10 | uint |     24 | 12 commanded joint angles in centidegrees
   *     34 | uint |      2 | voltage in millivolts
   *     36 | int  |      2 | current in milliamps
   *     38 | uint |      2 | main loop time in microseconds
   */
  telemetryPackage[0] = P_TELEMETRY;
  telemetryPackage[1] = P_TELEMETRY_VERSION;

  float imuData[3];
  float voltage;
  float current;
  legangle jointAngles[LEG_NUM];
  bool sampleValid;
  bool powerValid;
  unsigned long imuSampleAt;
  unsigned long powerSampleAt;

  portENTER_CRITICAL(&telemetryMux);
  imuData[0] = IMU_DATA[ROLL];
  imuData[1] = IMU_DATA[PITCH];
  imuData[2] = IMU_DATA[YAW];
  voltage = voltage_V;
  current = current_A;
  sampleValid = imuSampleValid;
  powerValid = powerSampleValid;
  imuSampleAt = imuSampleAtMicros;
  powerSampleAt = powerSampleAtMicros;
  for (int i = 0; i < LEG_NUM; i++) {
    jointAngles[i] = legs[i].angle;
  }
  portEXIT_CRITICAL(&telemetryMux);

  uint16_t status = 0;
  if (imuReady) status |= P_TELEMETRY_STATUS_IMU_READY;
  if (imuReady && sampleValid &&
      (unsigned long)(micros() - imuSampleAt) <= IMU_TELEMETRY_STALE_US) {
    status |= P_TELEMETRY_STATUS_IMU_VALID;
  }
  if (powerSensorReady) status |= P_TELEMETRY_STATUS_POWER_READY;
  if (powerSensorReady && powerValid &&
      (unsigned long)(micros() - powerSampleAt) <= POWER_TELEMETRY_STALE_US) {
    status |= P_TELEMETRY_STATUS_POWER_VALID;
  }
  if (movementArmed) status |= P_TELEMETRY_STATUS_ARMED;
  int16ToBytes(telemetryPackage, P_TELEMETRY_STATUS_OFFSET, status);

  for (int i = 0; i < 3; i++) {
    int16ToBytes(telemetryPackage, P_TELEMETRY_IMU_OFFSET + i * 2,
                 isfinite(imuData[i]) ? (int)(imuData[i] * 100.0f) : 0);
  }

  for (int i = 0; i < LEG_NUM; i++) {
    const double angles[3] = {
      jointAngles[i].alpha,
      jointAngles[i].beta,
      jointAngles[i].gamma
    };
    for (int j = 0; j < 3; j++) {
      double angleDeg = radToDeg(angles[j]);
      if (!isfinite(angleDeg)) angleDeg = 0.0;
      if (angleDeg < 0.0) angleDeg = 0.0;
      if (angleDeg > 180.0) angleDeg = 180.0;
      int16ToBytes(telemetryPackage, P_TELEMETRY_JOINTS_OFFSET + (i * 3 + j) * 2,
                   (int)(angleDeg * 100.0));
    }
  }

  int16ToBytes(telemetryPackage, P_TELEMETRY_VOLTAGE_OFFSET, (int)(voltage * 1000.0f));
  int16ToBytes(telemetryPackage, P_TELEMETRY_CURRENT_OFFSET, (int)(current * 1000.0f));
  int16ToBytes(telemetryPackage, P_TELEMETRY_LOOP_TIME_OFFSET, (int)loopTime);
}

void int16ToBytes(uint8_t* data, uint8_t offset, int value)
{
  data[offset]   = (value >> 8) & 0xFF;
  data[offset+1] =       value  & 0xFF;
}
