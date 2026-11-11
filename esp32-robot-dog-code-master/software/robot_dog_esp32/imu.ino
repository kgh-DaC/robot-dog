void initIMU()
{
  Serial.print("IMU (0x");
  Serial.print(IMU_ADDRESS, HEX);
  Serial.print("): ");
  setupIMU();
}

void setupIMU()
{
  imuReady = IMU.init();
  if (!imuReady) {
    Serial.println("does not respond");
    return;
  }

  IMU.setAccRange(MPU9250_ACC_RANGE_2G);
  IMU.enableAccDLPF(true);
  IMU.setAccDLPF(MPU9250_DLPF_6);
  Serial.println("ready");
}

double calibrateIMU(double id)
{
  Serial.println("Calibrating ACC and GYRO in 5 seconds. Put device on flat leveled surface.");
  delay(5000);
  Serial.print("Calibration...");
  IMU.autoOffsets();
  Serial.println("Done.");

  return 1;
}

void updateIMU()
{
  if (!imuReady) return;

  xyzFloat angle = IMU.getAngles();

  bool sampleValid = isfinite(angle.x) && isfinite(angle.y) && isfinite(angle.z);
  portENTER_CRITICAL(&telemetryMux);
  imuSampleAtMicros = micros();
  if (sampleValid) {
    // The sensor-to-body axis mapping has not yet been calibrated.
    IMU_DATA[ROLL]  = angle.x;
    IMU_DATA[PITCH] = angle.y;
    IMU_DATA[YAW]   = angle.z;
  }
  imuSampleValid = sampleValid;
  portEXIT_CRITICAL(&telemetryMux);
}
