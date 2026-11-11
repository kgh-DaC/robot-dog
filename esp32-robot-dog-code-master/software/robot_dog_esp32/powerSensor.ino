void initPowerSensor() {
  Serial.print("Power sensor ");
  #if POWER_SENSOR == INA219
    Serial.print("INA219");
  #else
    Serial.print("<NO>");
  #endif
  setupPowerSensor();
  Serial.println();
}

void updatePower() {
  float voltage = powerSensorReady ? ina219.getBusVoltage_V() : 0.0;
  float current = powerSensorReady ? ina219.getCurrent_mA() / 1000 : 0.0;
  bool sampleValid = powerSensorReady && isfinite(voltage) && isfinite(current);
  portENTER_CRITICAL(&telemetryMux);
  voltage_V = voltage;
  current_A = current;
  powerSampleValid = sampleValid;
  powerSampleAtMicros = micros();
  portEXIT_CRITICAL(&telemetryMux);
}

void setupPowerSensor()
{
  #if POWER_SENSOR == INA219
    if (ina219.init()) {
      powerSensorReady = true;
    } else {
      Serial.println("Failed to find INA219 chip");
    }

    ina219.setADCMode(INA219_BIT_MODE_9);  // just to make it fast
    ina219.setPGain(INA219_PG_320);
    ina219.setBusRange(INA219_BRNG_16);
  #endif
}

// return V
double getPowerSensorVoltage() {
  return voltage_V;
}


// return A
double getPowerSensorCurrent() {
  return current_A;
}
