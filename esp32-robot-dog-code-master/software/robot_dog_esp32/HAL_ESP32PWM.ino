#if PWM_CONTROLLER_TYPE == ESP32PWM
static const uint8_t SERVO_PWM_RESOLUTION = 14;
static const uint8_t SERVO_COUNT = LEG_NUM * 3;
int servoPins[SERVO_COUNT];

void initServoHAL() {
  Serial.print("LEDC ");
  for (uint8_t i = 0; i < LEG_NUM; i++) {
    servoPins[i * 3] = legs[i].hal.pin.alpha;
    servoPins[i * 3 + 1] = legs[i].hal.pin.beta;
    servoPins[i * 3 + 2] = legs[i].hal.pin.gamma;

    for (uint8_t j = 0; j < 3; j++) {
      uint8_t index = i * 3 + j;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
      bool attached = ledcAttach(servoPins[index], SERVO_FREQ, SERVO_PWM_RESOLUTION);
#else
      uint8_t channel = index;
      double attachedFrequency = ledcSetup(channel, SERVO_FREQ, SERVO_PWM_RESOLUTION);
      bool attached = attachedFrequency > 0;
      if (attached) ledcAttachPin(servoPins[index], channel);
#endif
      if (!attached) {
        Serial.print("Failed to attach servo PWM pin ");
        Serial.println(servoPins[index]);
      }
    }
  }
  Serial.println();
}

void writeServoPulse(uint8_t index, uint16_t pulseWidth) {
  const uint32_t maxDuty = (1UL << SERVO_PWM_RESOLUTION) - 1;
  const uint32_t duty = (uint64_t)pulseWidth * SERVO_FREQ * maxDuty / 1000000UL;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(servoPins[index], duty);
#else
  ledcWrite(index, duty);
#endif
}

uint16_t angleToPulse(double angleRad) {
  double angleDeg = radToDeg(angleRad);

  if (angleDeg < servoMainProfile.minAngle) angleDeg = servoMainProfile.minAngle;
  if (angleDeg > servoMainProfile.maxAngle) angleDeg = servoMainProfile.maxAngle;

  // TODO how to make it better???

  if (angleDeg < 30) {
    return mapf(angleDeg, servoMainProfile.minAngle, 30, servoMainProfile.degMin, servoMainProfile.deg30);
  }

  if (angleDeg < 50) {
    return mapf(angleDeg, 30, 50, servoMainProfile.deg30, servoMainProfile.deg50);
  }

  if (angleDeg < 70) {
    return mapf(angleDeg, 50, 70, servoMainProfile.deg50, servoMainProfile.deg70);
  }

  if (angleDeg < 90) {
    return mapf(angleDeg, 70, 90, servoMainProfile.deg70, servoMainProfile.deg90);
  }

  if (angleDeg < 110) {
    return mapf(angleDeg, 90, 110, servoMainProfile.deg90, servoMainProfile.deg110);
  }

  if (angleDeg < 130) {
    return mapf(angleDeg, 110, 130, servoMainProfile.deg110, servoMainProfile.deg130);
  }

  if (angleDeg < 150) {
    return mapf(angleDeg, 130, 150, servoMainProfile.deg130, servoMainProfile.deg150);
  }

  if (angleDeg <= servoMainProfile.maxAngle) {
    return mapf(angleDeg, 150, servoMainProfile.maxAngle, servoMainProfile.deg150, servoMainProfile.degMax);
  }


  return 1500;  // TODO actualy we should fail here...
}


void setLegPWM(leg &_leg)
{
  uint8_t l = _leg.id.id*3;
  uint16_t pulseWidth = angleToPulse(limitServoAngle(getHALAngle(_leg.angle.alpha, _leg.hal.mid.alpha, _leg.hal.trim.alpha, _leg.hal.ratio.alpha, _leg.inverse.alpha)));
  writeServoPulse(l + 0, pulseWidth);

  pulseWidth = angleToPulse(limitServoAngle(getHALAngle(_leg.angle.beta, _leg.hal.mid.beta, _leg.hal.trim.beta, _leg.hal.ratio.beta, _leg.inverse.beta)));
  writeServoPulse(l + 1, pulseWidth);

  pulseWidth = angleToPulse(limitServoAngle(getHALAngle(_leg.angle.gamma, _leg.hal.mid.gamma, _leg.hal.trim.gamma, _leg.hal.ratio.gamma, _leg.inverse.gamma)));
  writeServoPulse(l + 2, pulseWidth);
}

void runServoCalibrate(leg &_leg)
{
  uint8_t l = _leg.id.id*3;
  writeServoPulse(l + 0, servoMainProfile.deg90);
  writeServoPulse(l + 1, servoMainProfile.deg90);
  writeServoPulse(l + 2, servoMainProfile.deg90);
}

#endif
