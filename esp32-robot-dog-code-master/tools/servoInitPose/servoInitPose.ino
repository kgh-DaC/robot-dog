#include <EEPROM.h>
#include <Preferences.h>
#include <esp_arduino_version.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <esp_system.h>
#if ESP_ARDUINO_VERSION_MAJOR < 3
#include "ESP32_ISR_Servo.h"
#endif

struct ServoConfig {
  uint8_t pin;
  uint8_t targetAngle;
  double gearRatio;
  bool inverse;
  double trim;
};

ServoConfig servos[] = {
  {25, 90, 2, false, 0}, {26, 45, 1, true,  0}, {27, 90, 1, true,  0},
  {16, 90, 2, true,  0}, {18, 45, 1, false, 0}, {17, 90, 1, false, 0},
  {13, 90, 2, true,  0}, {12, 45, 1, true,  0}, {14, 90, 1, true,  0},
  { 4, 86, 2, false, 0}, { 2, 45, 1, false, 0}, {15, 90, 1, false, 0}
};

// Standing-pose GAMMA trim per leg (LF, RF, LH, RH), logical degrees, added to
// every commanded GAMMA angle (STATUS/SET still use the untrimmed 90-based
// angles). Measured 2026-11-11 on a flat floor: with all GAMMA at 90 the body
// stood at roll -5.4 (left low) / pitch +2.5 (nose low), confirmed by a phone
// level. These values brought it to roll +0.5 / pitch -0.1. A higher GAMMA
// raises that corner of the body. Feet friction gives ~2 deg of hysteresis.
// 2026-11-11 later: after power-up and after each short walk the body settled
// at roll +1.6..+2.3 (left high, user confirmed by eye), so the left/right
// split was reduced by ~4 deg (0.45 deg roll per deg GAMMA).
const float STAND_GAMMA_TRIM_DEG[4] = {9.0f, 1.0f, -1.0f, -9.0f};

const uint8_t SERVO_COUNT = sizeof(servos) / sizeof(servos[0]);
const uint16_t SERVO_MIN = 600;
const uint16_t SERVO_MAX = 2400;
const uint8_t SERVO_MIN_ANGLE = 20;
const uint8_t SERVO_MAX_ANGLE = 160;
const uint8_t MAX_JOINT_OFFSET = 15;
const int SWING_LIFT_GAMMA_DEG = 11;  // knee fold of the swing leg while walking (8 dragged, 14 tipped)
// 2026-11-12 gait rework (not yet tried on hardware): the old gait moved the
// lifted legs and the pushing legs at the same moment with a +-5 deg stride, so
// feet dragged and the body advanced only ~5 mm in 5 s. Now each half-cycle has
// three steps: lift the swing pair, swing it forward while the stance pair pushes
// back, lower it. Tune these first if the robot tips, slips or browns out.
const int WALK_STRIDE_DEG = 7;            // BETA offset at each end of the stroke (was 5)
const int WALK_SIDE_DEG = 4;              // ALPHA offset for LEFT/RIGHT turns (unchanged)
const int WALK_TURN_BETA_DEG = 3;         // BETA offset for turns (unchanged)
const uint16_t WALK_LIFT_MS = 350;        // time to lift the swing pair
const uint16_t WALK_MOVE_MS = 600;        // swing forward / push back
const uint16_t WALK_LOWER_MS = 300;       // time to put the swing pair down
const uint16_t WALK_PULSE_STEP_US = 8;    // servo slew while walking (was 5; 8 us/20 ms = ~40 deg/s)
const uint16_t PULSE_STEP_US = 5;
const uint16_t PULSE_STEP_INTERVAL_MS = 20;
// Boot homing: slower slew (2 us / 20 ms = 100 us/s, about 10 deg/s at the horn)
// from the last saved pose to neutral.
const uint16_t STARTUP_PULSE_STEP_US = 2;
// Save the pose once it has been still this long. Also applies while walking,
// so an unexpected reset (e.g. brownout) restarts from a pose close to the real one.
const uint16_t POSE_SAVE_SETTLE_MS = 300;
// Minimum time between flash writes (limits flash wear while walking).
const uint16_t POSE_SAVE_MIN_INTERVAL_MS = 1000;
const uint32_t POSE_MAGIC = 0x50534531; // "PSE1"
const uint32_t SERVO_PWM_FREQUENCY = 50;
const uint8_t SERVO_PWM_RESOLUTION = 16;
const uint16_t SERVO_PULSES[] = {780, 890, 1090, 1300, 1500, 1700, 1900, 2110, 2200};
const uint8_t SERVO_ANGLES[] = {20, 30, 50, 70, 90, 110, 130, 150, 160};
const char *LEG_NAMES[] = {"LF", "RF", "LH", "RH"};
const char *JOINT_NAMES[] = {"ALPHA", "BETA", "GAMMA"};

int servoIds[SERVO_COUNT];
uint16_t currentPulses[SERVO_COUNT];
uint16_t targetPulses[SERVO_COUNT];
char commandBuffer[128];  // WIFI_SET <32-byte ssid><TAB><63-char password> fits
uint8_t commandLength = 0;
unsigned long lastPulseUpdate = 0;
bool servosReady = false;
bool startupHoming = false;
Preferences poseStore;
bool poseStoreReady = false;
uint16_t savedPulses[SERVO_COUNT];
unsigned long poseSettledSince = 0;
unsigned long lastPoseSaveAt = 0;
bool poseSettled = false;
enum WalkDirection { WALK_STOPPED, WALK_FORWARD, WALK_BACKWARD, WALK_LEFT, WALK_RIGHT };
WalkDirection walkDirection = WALK_STOPPED;
uint8_t walkPhase = 0;   // which diagonal pair swings: 0 = LF+RH, 1 = RF+LH
uint8_t walkStep = 0;    // 0 lift, 1 swing/push, 2 lower
bool walkFresh = false;  // first half-cycle after the walk starts: legs begin at neutral
unsigned long nextWalkPhaseAt = 0;

// Remote control (USB gateway, Wi-Fi web page or the 4way notebook). Every walk
// runs under a lease: the controller must send a heartbeat (HB, WALKHB or the
// legacy WALK) at least every LEASE_TIMEOUT_MS or the robot stops and disarms.
// Legacy serial "WALK <dir>" arms the serial controller implicitly, so it is
// covered by the same lease. STOP is accepted from any source at any time.
// Nothing re-arms automatically after a reset.
enum Controller { CTRL_NONE, CTRL_SERIAL, CTRL_WIFI };
const uint16_t LEASE_TIMEOUT_MS = 1000;
Controller controller = CTRL_NONE;
bool armed = false;
unsigned long leaseDeadline = 0;
const char *lastStopReason = "boot";

// The web server runs in its own FreeRTOS task (web_control.ino) because
// WebServer::handleClient() can block for seconds on a slow client. All
// control/servo state is shared, so loop() and every web handler hold this
// mutex while touching it. Network I/O is never done while holding it.
SemaphoreHandle_t controlMutex = NULL;

double mapValue(double value, double inputMin, double inputMax, double outputMin, double outputMax)
{
  return (value - inputMin) * (outputMax - outputMin) / (inputMax - inputMin) + outputMin;
}

uint16_t angleToPulse(double angle)
{
  double degrees = angle * 180.0 / M_PI;
  if (degrees < SERVO_MIN_ANGLE) degrees = SERVO_MIN_ANGLE;
  if (degrees > SERVO_MAX_ANGLE) degrees = SERVO_MAX_ANGLE;

  for (int i = 0; i < 8; i++) {
    if (degrees < SERVO_ANGLES[i + 1]) {
      return mapValue(degrees, SERVO_ANGLES[i], SERVO_ANGLES[i + 1], SERVO_PULSES[i], SERVO_PULSES[i + 1]);
    }
  }
  return SERVO_PULSES[8];
}

// Body-leveling offset per servo (deg, logical angle), set only by
// level_control.ino while armed; 0 otherwise. Added to the commanded angle.
float levelOffsetDeg[SERVO_COUNT] = {0};

uint16_t pulseForServo(uint8_t servoIndex)
{
  const double standTrim = servoIndex % 3 == 2 ? STAND_GAMMA_TRIM_DEG[servoIndex / 3] : 0.0;
  const double commanded = servos[servoIndex].targetAngle + levelOffsetDeg[servoIndex] + standTrim;
  double angle = (commanded - 90) * M_PI / 180.0 * servos[servoIndex].gearRatio + M_PI_2;
  if (servos[servoIndex].inverse) angle = M_PI - angle;
  angle += servos[servoIndex].trim;
  if (angle < 0) angle = 0;
  if (angle > M_PI) angle = M_PI;
  return angleToPulse(angle);
}

bool initializeServo(uint8_t servoIndex)
{
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(servos[servoIndex].pin, SERVO_PWM_FREQUENCY, SERVO_PWM_RESOLUTION);
#else
  servoIds[servoIndex] = ESP32_ISR_Servos.setupServo(servos[servoIndex].pin, SERVO_MIN, SERVO_MAX);
  return servoIds[servoIndex] >= 0;
#endif
}

bool writeServoPulse(uint8_t servoIndex, uint16_t pulseWidth)
{
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  const uint64_t maxDuty = (1ULL << SERVO_PWM_RESOLUTION) - 1;
  const uint32_t duty = (uint64_t)pulseWidth * SERVO_PWM_FREQUENCY * maxDuty / 1000000ULL;
  return ledcWrite(servos[servoIndex].pin, duty);
#else
  return ESP32_ISR_Servos.setPulseWidth(servoIds[servoIndex], pulseWidth);
#endif
}

bool setLfGammaTestPin(uint8_t newPin)
{
  const uint8_t servoIndex = 2;
  if (newPin != 27 && newPin != 32 && newPin != 33) return false;
  for (int i = 0; i < SERVO_COUNT; i++) {
    if (i != servoIndex && servos[i].pin == newPin) return false;
  }
  if (servos[servoIndex].pin == newPin) return true;

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  const uint8_t oldPin = servos[servoIndex].pin;
  if (!ledcDetach(oldPin)) return false;
  if (!ledcAttach(newPin, SERVO_PWM_FREQUENCY, SERVO_PWM_RESOLUTION)) {
    ledcAttach(oldPin, SERVO_PWM_FREQUENCY, SERVO_PWM_RESOLUTION);
    writeServoPulse(servoIndex, currentPulses[servoIndex]);
    return false;
  }
  servos[servoIndex].pin = newPin;
  if (writeServoPulse(servoIndex, currentPulses[servoIndex])) return true;

  ledcDetach(newPin);
  servos[servoIndex].pin = oldPin;
  ledcAttach(oldPin, SERVO_PWM_FREQUENCY, SERVO_PWM_RESOLUTION);
  writeServoPulse(servoIndex, currentPulses[servoIndex]);
  return false;
#else
  return false;
#endif
}

int legIndex(const char *name)
{
  for (int i = 0; i < 4; i++) {
    if (strcmp(name, LEG_NAMES[i]) == 0) return i;
  }
  return -1;
}

int jointIndex(const char *name)
{
  for (int i = 0; i < 3; i++) {
    if (strcmp(name, JOINT_NAMES[i]) == 0) return i;
  }
  return -1;
}

bool validAngle(int servoIndex, int angle)
{
  int base = neutralAngle(servoIndex);
  return angle >= base - MAX_JOINT_OFFSET && angle <= base + MAX_JOINT_OFFSET;
}

void setTargetAngle(int servoIndex, int angle)
{
  servos[servoIndex].targetAngle = angle;
  targetPulses[servoIndex] = pulseForServo(servoIndex);
}

// Initial (neutral) angle per joint. RH ALPHA (index 9) is 86 by user request
// 2026-11-13; all other joints keep 90 (ALPHA/GAMMA) or 45 (BETA).
int neutralAngle(int servoIndex)
{
  if (servoIndex == 9) return 86;
  return servoIndex % 3 == 1 ? 45 : 90;
}

// Pose for the current (walkPhase, walkStep). The swing pair S ends its stance
// at the "stance end" of the stroke and the other pair T ends its swing at the
// "swing end"; during step 1 S goes to the swing end (in the air) while T goes
// to the stance end (on the floor, pushing the body forward).
void setWalkingPose()
{
  int angles[SERVO_COUNT];
  for (int i = 0; i < SERVO_COUNT; i++) angles[i] = neutralAngle(i);

  const int swingPairA[] = {0, 9}; // LF + RH
  const int swingPairB[] = {3, 6}; // RF + LH
  const int *swingPair = walkPhase == 0 ? swingPairA : swingPairB;
  // Signs keep the travel direction confirmed on hardware 2026-11-11: FORWARD
  // puts the swing leg at BETA +stride and the pushing leg at BETA -stride.
  const int stride = walkDirection == WALK_BACKWARD ? WALK_STRIDE_DEG : -WALK_STRIDE_DEG;
  const int side = walkDirection == WALK_LEFT ? WALK_SIDE_DEG : -WALK_SIDE_DEG;
  const bool straight = walkDirection == WALK_FORWARD || walkDirection == WALK_BACKWARD;

  for (int leg = 0; leg < 4; leg++) {
    const int base = leg * 3;
    const bool swing = leg == swingPair[0] / 3 || leg == swingPair[1] / 3;
    // Is this leg at the swing end of its stroke in this step?
    const bool atSwingEnd = swing ? walkStep >= 1 : walkStep == 0;
    if (!(walkFresh && walkStep == 0)) {
      const int sign = atSwingEnd ? 1 : -1;       // swing end +, stance end -
      if (straight) {
        angles[base + 1] += sign * -stride;
      } else {
        angles[base] += sign * side;
        angles[base + 1] += sign * WALK_TURN_BETA_DEG;
      }
    }
    // Lift only while the swing leg is actually in the air (steps 0 and 1).
    // A LOWER GAMMA shortens the leg (measured standing, 2026-11-11). Lift
    // history: +8 pushed into the floor, -8 dragged, -14 tipped (8.6 deg),
    // -11 is the current value.
    if (swing && walkStep < 2) angles[base + 2] -= SWING_LIFT_GAMMA_DEG;
  }

  for (int i = 0; i < SERVO_COUNT; i++) {
    setTargetAngle(i, angles[i]);
  }
}

void stopWalking()
{
  walkDirection = WALK_STOPPED;
  for (int i = 0; i < SERVO_COUNT; i++) {
    setTargetAngle(i, neutralAngle(i));
  }
}

void updateWalking()
{
  if (walkDirection == WALK_STOPPED) return;
  const unsigned long now = millis();
  if ((long)(now - nextWalkPhaseAt) < 0) return;

  setWalkingPose();
  const uint16_t holdMs = walkStep == 0 ? WALK_LIFT_MS : walkStep == 1 ? WALK_MOVE_MS : WALK_LOWER_MS;
  nextWalkPhaseAt = now + holdMs;
  if (++walkStep >= 3) {
    walkStep = 0;
    walkPhase = 1 - walkPhase;
    walkFresh = false;
  }
}

const char *controllerName(Controller value)
{
  return value == CTRL_SERIAL ? "SERIAL" : value == CTRL_WIFI ? "WIFI" : "NONE";
}

const char *walkName(WalkDirection value)
{
  switch (value) {
    case WALK_FORWARD: return "FORWARD";
    case WALK_BACKWARD: return "BACKWARD";
    case WALK_LEFT: return "LEFT";
    case WALK_RIGHT: return "RIGHT";
    default: return "STOPPED";
  }
}

bool parseWalkDirection(const char *text, WalkDirection &value)
{
  if (text == NULL) return false;
  if (strcmp(text, "STOP") == 0) value = WALK_STOPPED;  // stop walking, stay armed
  else if (strcmp(text, "FORWARD") == 0) value = WALK_FORWARD;
  else if (strcmp(text, "BACKWARD") == 0) value = WALK_BACKWARD;
  else if (strcmp(text, "LEFT") == 0) value = WALK_LEFT;
  else if (strcmp(text, "RIGHT") == 0) value = WALK_RIGHT;
  else return false;
  return true;
}

// Stop walking, return all joints to neutral (slewed) and drop any arm/lease.
void controlStop(const char *reason)
{
  stopWalking();
  armed = false;
  controller = CTRL_NONE;
  lastStopReason = reason;
  levelOnStop();  // leveling off, compensation cleared: plain neutral pose
}

// Each control* function returns NULL on success or an error code.
const char *controlArm(Controller source)
{
  if (!servosReady) return "servos_not_ready";
  if (startupHoming) return "homing_in_progress";
  if (armed && controller != source) return "controlled_by_other";
  if (!armed && walkDirection != WALK_STOPPED) return "walking_active";
  const char *levelError = levelArmCheck();
  if (levelError != NULL) return levelError;
  if (!armed) levelOnArm();
  armed = true;
  controller = source;
  leaseDeadline = millis() + LEASE_TIMEOUT_MS;
  return NULL;
}

const char *controlHeartbeat(Controller source)
{
  if (!armed || controller != source) return "not_armed";
  leaseDeadline = millis() + LEASE_TIMEOUT_MS;
  return NULL;
}

const char *controlWalk(Controller source, WalkDirection direction)
{
  if (!armed || controller != source) return "not_armed";
  if (direction == WALK_STOPPED) {
    stopWalking();
  } else if (direction != walkDirection) {
    walkDirection = direction;
    walkPhase = 0;
    walkStep = 0;
    walkFresh = true;
    nextWalkPhaseAt = millis();
  }
  leaseDeadline = millis() + LEASE_TIMEOUT_MS;
  return NULL;
}

void updateLease()
{
  if (!armed) return;
  if ((long)(millis() - leaseDeadline) < 0) return;
  controlStop("heartbeat_timeout");
  Serial.println("EVENT STOPPED heartbeat_timeout");
}

void printState()
{
  Serial.print("STATE armed=");
  Serial.print(armed ? 1 : 0);
  Serial.print(" ctrl=");
  Serial.print(controllerName(controller));
  Serial.print(" walk=");
  Serial.print(walkName(walkDirection));
  Serial.print(" homing=");
  Serial.print(startupHoming ? 1 : 0);
  Serial.print(" settled=");
  Serial.print(poseSettledAndSaved() ? 1 : 0);
  Serial.print(" reset=");
  Serial.print(resetReasonName());
  Serial.print(" stop=");
  Serial.print(lastStopReason);
  Serial.print(" uptime=");
  Serial.println(millis());
}

void printStatus()
{
  Serial.print("STATUS");
  for (int i = 0; i < 4; i++) {
    int base = i * 3;
    Serial.print(' ');
    Serial.print(LEG_NAMES[i]);
    Serial.print('=');
    Serial.print(servos[base].targetAngle);
    Serial.print('/');
    Serial.print(servos[base + 1].targetAngle);
    Serial.print('/');
    Serial.print(servos[base + 2].targetAngle);
  }
  Serial.println();
}

bool parseAngle(const char *text, int &angle)
{
  if (text == NULL || *text == '\0') return false;
  char *end = NULL;
  long value = strtol(text, &end, 10);
  if (*end != '\0' || value < 0 || value > 180) return false;
  angle = (int)value;
  return true;
}

const char *resetReasonName()
{
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXTERNAL";
    case ESP_RST_SW: return "SOFTWARE";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO: return "SDIO";
    default: return "UNKNOWN";
  }
}

// True when not walking, every servo has reached its target and that pose is
// saved in flash, so a reset now would restart from the real servo positions.
bool poseSettledAndSaved()
{
  if (walkDirection != WALK_STOPPED) return false;
  for (int i = 0; i < SERVO_COUNT; i++) {
    if (currentPulses[i] != targetPulses[i]) return false;
  }
  return !poseStoreReady || memcmp(savedPulses, currentPulses, sizeof(savedPulses)) == 0;
}

void processCommand(char *line)
{
  // Hotspot settings: parsed before strtok because the SSID may contain spaces.
  // WIFI_SET <ssid><TAB><password>  (empty password = open network)
  if (strncmp(line, "WIFI_SET ", 9) == 0 || strcmp(line, "WIFI_CLEAR") == 0) {
    const bool clear = line[5] == 'C';
    char *tab = clear ? NULL : strchr(line + 9, '\t');
    const char *error = clear ? wifiStaClear() : tab == NULL ? "format_USE_WIFI_SET_ssid_TAB_password" : NULL;
    if (error == NULL && !clear) {
      *tab = '\0';
      error = wifiStaSet(line + 9, tab + 1);
    }
    if (error != NULL) {
      Serial.print("ERR ");
      Serial.println(error);
      return;
    }
    Serial.println(clear ? "OK WIFI_CLEAR" : "OK WIFI_SET");
    return;
  }

  char *save = NULL;
  char *command = strtok_r(line, " \t", &save);
  if (command == NULL) return;

  if (strcmp(command, "HELLO") == 0) {
    Serial.println("READY SERVO_POSE_V1");
    return;
  }

  if (strcmp(command, "RESET_REASON") == 0) {
    Serial.print("RESET_REASON ");
    Serial.println(resetReasonName());
    return;
  }

  if (strcmp(command, "SETTLED") == 0) {
    Serial.println(poseSettledAndSaved() ? "SETTLED 1" : "SETTLED 0");
    return;
  }

  if (strcmp(command, "CAPS") == 0) {
    Serial.println("CAPS SERVO_POSE_V1 WALK4 LEASE1 WIFI1 IMU1 LEVEL1 STA1");
    return;
  }

  if (strcmp(command, "LEVEL") == 0) {  // LEVEL | LEVEL ON | LEVEL OFF
    char *mode = strtok_r(NULL, " \t", &save);
    if (mode == NULL) {
      printLevel();
      return;
    }
    if ((strcmp(mode, "ON") != 0 && strcmp(mode, "OFF") != 0) || strtok_r(NULL, " \t", &save) != NULL) {
      Serial.println("ERR format_USE_LEVEL_ON_OFF");
      return;
    }
    const bool on = strcmp(mode, "ON") == 0;
    const char *error = levelSet(on, !armed || controller == CTRL_SERIAL);
    if (error != NULL) {
      Serial.print("ERR ");
      Serial.println(error);
      return;
    }
    Serial.println(on ? "OK LEVEL ON" : "OK LEVEL OFF");
    return;
  }

  if (strcmp(command, "IMU") == 0) {
    printImu();
    return;
  }

  if (strcmp(command, "I2C") == 0) {
    printI2cScan();
    return;
  }

  if (strcmp(command, "I2CREAD") == 0) {  // diagnostics, read only: I2CREAD 69 75 [n]
    char *addrText = strtok_r(NULL, " \t", &save);
    char *regText = strtok_r(NULL, " \t", &save);
    char *countText = strtok_r(NULL, " \t", &save);
    char *end = NULL;
    const long addr = addrText ? strtol(addrText, &end, 16) : -1;
    const bool addrOk = addrText && *end == '\0' && addr > 0 && addr < 0x78;
    const long reg = regText ? strtol(regText, &end, 16) : -1;
    const bool regOk = regText && *end == '\0' && reg >= 0 && reg <= 0xFF;
    const long count = countText ? strtol(countText, &end, 10) : 1;
    const bool countOk = !countText || (*end == '\0' && count >= 1 && count <= 16);
    if (!addrOk || !regOk || !countOk || strtok_r(NULL, " \t", &save) != NULL) {
      Serial.println("ERR format_USE_I2CREAD_addr_reg_count");
      return;
    }
    printI2cRead((uint8_t)addr, (uint8_t)reg, (uint8_t)count);
    return;
  }

  if (strcmp(command, "IMUZERO") == 0 || strcmp(command, "IMUMAP") == 0) {
    const bool zero = command[3] == 'Z';
    char *first = strtok_r(NULL, " \t", &save);
    char *second = first == NULL ? NULL : strtok_r(NULL, " \t", &save);
    const char *error = NULL;
    if (zero) error = first != NULL ? "format_USE_IMUZERO" : imuZero();
    else if (first == NULL || strtok_r(NULL, " \t", &save) != NULL) error = "format_USE_IMUMAP_roll_pitch";
    else error = imuSetMap(first, second);
    if (error != NULL) {
      Serial.print("ERR ");
      Serial.println(error);
      return;
    }
    Serial.println(zero ? "OK IMUZERO" : "OK IMUMAP");
    return;
  }

  if (strcmp(command, "WIFI") == 0) {
    printWifiInfo();
    return;
  }

  if (!servosReady) {
    Serial.println("ERR servos_not_ready");
    return;
  }

  if (strcmp(command, "STATE") == 0) {
    printState();
    return;
  }

  if (strcmp(command, "ARM") == 0 || strcmp(command, "HB") == 0) {
    const bool arm = command[0] == 'A';
    const char *error = arm ? controlArm(CTRL_SERIAL) : controlHeartbeat(CTRL_SERIAL);
    if (error != NULL) {
      Serial.print("ERR ");
      Serial.println(error);
      return;
    }
    Serial.println(arm ? "OK ARM" : "OK HB");
    return;
  }

  if (strcmp(command, "DISARM") == 0) {
    controlStop("disarm");
    Serial.println("OK DISARM");
    return;
  }

  if (strcmp(command, "WALKHB") == 0) {
    char *direction = strtok_r(NULL, " \t", &save);
    WalkDirection parsed = WALK_STOPPED;
    if (direction == NULL || strtok_r(NULL, " \t", &save) != NULL ||
        !parseWalkDirection(direction, parsed)) {
      Serial.println("ERR format_USE_WALKHB_direction");
      return;
    }
    const char *error = controlWalk(CTRL_SERIAL, parsed);
    if (error != NULL) {
      Serial.print("ERR ");
      Serial.println(error);
      return;
    }
    Serial.print("OK WALKHB ");
    Serial.println(direction);
    return;
  }

  if (strcmp(command, "WALK") == 0) {
    char *direction = strtok_r(NULL, " \t", &save);
    if (direction == NULL || strtok_r(NULL, " \t", &save) != NULL) {
      Serial.println("ERR format_USE_WALK_direction");
      return;
    }
    if (strcmp(direction, "STOP") == 0) {
      controlStop("serial_stop");
      Serial.println("OK WALK STOPPED");
      return;
    }
    if (armed && controller != CTRL_SERIAL) {
      Serial.println("ERR controlled_by_remote");
      return;
    }
    WalkDirection parsed = WALK_STOPPED;
    if (!parseWalkDirection(direction, parsed) || parsed == WALK_STOPPED) {
      Serial.println("ERR invalid_walk_direction");
      return;
    }
    // Implicit serial ARM: the sender must keep the lease alive with HB.
    const char *error = controlArm(CTRL_SERIAL);
    if (error == NULL) error = controlWalk(CTRL_SERIAL, parsed);
    if (error != NULL) {
      Serial.print("ERR ");
      Serial.println(error);
      return;
    }
    Serial.print("OK WALK ");
    Serial.println(direction);
    return;
  }

  if (strcmp(command, "STATUS") == 0) {
    printStatus();
    return;
  }

  if (strcmp(command, "PIN") == 0) {
    if (armed) {
      Serial.println("ERR controlled_by_remote");
      return;
    }
    char *legName = strtok_r(NULL, " \t", &save);
    char *jointName = strtok_r(NULL, " \t", &save);
    char *pinText = strtok_r(NULL, " \t", &save);
    int pin = -1;
    if (legName == NULL || jointName == NULL || pinText == NULL ||
        strtok_r(NULL, " \t", &save) != NULL ||
        strcmp(legName, "LF") != 0 || strcmp(jointName, "GAMMA") != 0 ||
        !parseAngle(pinText, pin)) {
      Serial.println("ERR format_USE_PIN_LF_GAMMA_gpio");
      return;
    }
    if (walkDirection != WALK_STOPPED) {
      Serial.println("ERR walking_active");
      return;
    }
    if (!setLfGammaTestPin((uint8_t)pin)) {
      Serial.println("ERR pin_unavailable_or_unsupported");
      return;
    }
    Serial.print("OK PIN LF GAMMA ");
    Serial.println(pin);
    return;
  }

  if (strcmp(command, "CENTER") == 0) {
    if (armed) {
      Serial.println("ERR controlled_by_remote");
      return;
    }
    walkDirection = WALK_STOPPED;
    for (int i = 0; i < SERVO_COUNT; i++) {
      setTargetAngle(i, neutralAngle(i));
    }
    Serial.println("OK CENTER");
    return;
  }

  if (strcmp(command, "SET") == 0) {
    if (armed) {
      Serial.println("ERR controlled_by_remote");
      return;
    }
    if (walkDirection != WALK_STOPPED) {
      Serial.println("ERR walking_active");
      return;
    }
    char *legName = strtok_r(NULL, " \t", &save);
    char *jointName = strtok_r(NULL, " \t", &save);
    char *angleText = strtok_r(NULL, " \t", &save);
    if (legName == NULL || jointName == NULL || angleText == NULL || strtok_r(NULL, " \t", &save) != NULL) {
      Serial.println("ERR format_USE_SET_leg_joint_angle");
      return;
    }

    int leg = legIndex(legName);
    int joint = jointIndex(jointName);
    int angle = 0;
    if (leg < 0 || joint < 0 || !parseAngle(angleText, angle)) {
      Serial.println("ERR invalid_leg_joint_or_angle");
      return;
    }

    int index = leg * 3 + joint;
    if (!validAngle(index, angle)) {
      Serial.println("ERR angle_out_of_safe_range");
      return;
    }

    setTargetAngle(index, angle);
    Serial.print("OK SET ");
    Serial.print(LEG_NAMES[leg]);
    Serial.print(' ');
    Serial.print(JOINT_NAMES[joint]);
    Serial.print(' ');
    Serial.println(angle);
    return;
  }

  if (strcmp(command, "POSE") == 0) {
    if (armed) {
      Serial.println("ERR controlled_by_remote");
      return;
    }
    if (walkDirection != WALK_STOPPED) {
      Serial.println("ERR walking_active");
      return;
    }
    char *alphaText = strtok_r(NULL, " \t", &save);
    char *betaText = strtok_r(NULL, " \t", &save);
    char *gammaText = strtok_r(NULL, " \t", &save);
    if (alphaText == NULL || betaText == NULL || gammaText == NULL || strtok_r(NULL, " \t", &save) != NULL) {
      Serial.println("ERR format_USE_POSE_alpha_beta_gamma");
      return;
    }

    int angles[3] = {0, 0, 0};
    int index = 0;
    for (; index < 3; index++) {
      if (!parseAngle(index == 0 ? alphaText : index == 1 ? betaText : gammaText, angles[index]) ||
          !validAngle(index, angles[index])) {
        break;
      }
    }
    if (index != 3) {
      Serial.println("ERR pose_out_of_safe_range");
      return;
    }

    for (int leg = 0; leg < 4; leg++) {
      for (int joint = 0; joint < 3; joint++) {
        setTargetAngle(leg * 3 + joint, angles[joint]);
      }
    }
    Serial.println("OK POSE");
    return;
  }

  Serial.println("ERR unknown_command");
}

void readSerialCommands()
{
  while (Serial.available() > 0) {
    char value = Serial.read();
    if (value == '\r') continue;
    if (value == '\n') {
      commandBuffer[commandLength] = '\0';
      processCommand(commandBuffer);
      commandLength = 0;
    } else if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = value;
    } else {
      commandLength = 0;
      Serial.println("ERR command_too_long");
    }
  }
}

bool loadSavedPulses(uint16_t *pulses)
{
  if (!poseStoreReady || poseStore.getUInt("magic", 0) != POSE_MAGIC) return false;
  if (poseStore.getBytes("pulses", pulses, sizeof(uint16_t) * SERVO_COUNT) != sizeof(uint16_t) * SERVO_COUNT) return false;
  for (int i = 0; i < SERVO_COUNT; i++) {
    if (pulses[i] < SERVO_PULSES[0] || pulses[i] > SERVO_PULSES[8]) return false;
  }
  return true;
}

void savePoseIfSettled()
{
  if (!poseStoreReady) return;
  for (int i = 0; i < SERVO_COUNT; i++) {
    if (currentPulses[i] != targetPulses[i]) {
      poseSettled = false;
      return;
    }
  }
  const unsigned long now = millis();
  if (!poseSettled) {
    poseSettled = true;
    poseSettledSince = now;
    return;
  }
  if (now - poseSettledSince < POSE_SAVE_SETTLE_MS) return;
  if (memcmp(savedPulses, currentPulses, sizeof(savedPulses)) == 0) return;
  if (lastPoseSaveAt != 0 && now - lastPoseSaveAt < POSE_SAVE_MIN_INTERVAL_MS) return;

  lastPoseSaveAt = now;
  if (poseStore.putBytes("pulses", currentPulses, sizeof(currentPulses)) == sizeof(currentPulses)) {
    if (poseStore.getUInt("magic", 0) != POSE_MAGIC) poseStore.putUInt("magic", POSE_MAGIC);
    memcpy(savedPulses, currentPulses, sizeof(savedPulses));
  }
}

void updateServoPulses()
{
  unsigned long now = millis();
  if (now - lastPulseUpdate < PULSE_STEP_INTERVAL_MS) return;
  lastPulseUpdate = now;

  const int step = startupHoming ? STARTUP_PULSE_STEP_US :
                  walkDirection != WALK_STOPPED ? WALK_PULSE_STEP_US : PULSE_STEP_US;
  bool allReached = true;
  for (int i = 0; i < SERVO_COUNT; i++) {
    if (currentPulses[i] == targetPulses[i]) continue;
    if (currentPulses[i] < targetPulses[i]) {
      currentPulses[i] = min((int)currentPulses[i] + step, (int)targetPulses[i]);
    } else {
      currentPulses[i] = max((int)currentPulses[i] - step, (int)targetPulses[i]);
    }
    if (currentPulses[i] != targetPulses[i]) allReached = false;
    uint16_t pulseWidth = currentPulses[i];
    writeServoPulse(i, pulseWidth);
  }
  if (startupHoming && allReached) {
    startupHoming = false;
    Serial.println("HOMING_DONE");
  }
}

void setup()
{
  Serial.begin(115200);
  controlMutex = xSemaphoreCreateMutex();
  if (controlMutex == NULL) {
    // loop() does nothing without the mutex: no servo pulses, no motion.
    Serial.println("ERROR: control mutex allocation failed");
    return;
  }

  if (!EEPROM.begin(32)) {
    Serial.println("ERROR: EEPROM initialization failed");
    return;
  }

  if (EEPROM.read(0) == 1 && EEPROM.read(1) == 1) {
    for (int i = 0; i < SERVO_COUNT; i++) {
      servos[i].trim = ((int)EEPROM.read(i + 2) - 128) / 500.0;
    }
  }

  servosReady = true;
  for (int i = 0; i < SERVO_COUNT; i++) {
    if (!initializeServo(i)) servosReady = false;
  }

  if (!servosReady) {
    Serial.println("ERROR: Unable to initialize all 12 servos");
    return;
  }

  // ledcAttach() starts at duty 0 (no servo pulse), so drive every servo now.
  // Hobby servos have no position feedback: start from the last settled pose
  // saved in flash (where the servos most likely still are) and slew slowly to
  // neutral. Without a valid saved pose, fall back to neutral directly.
  poseStoreReady = poseStore.begin("servoPose", false);
  const bool restored = loadSavedPulses(savedPulses);
  for (int i = 0; i < SERVO_COUNT; i++) {
    targetPulses[i] = pulseForServo(i);
    currentPulses[i] = restored ? savedPulses[i] : targetPulses[i];
    if (!restored) savedPulses[i] = 0;
    if (!writeServoPulse(i, currentPulses[i])) servosReady = false;
  }

  if (!servosReady) {
    Serial.println("ERROR: Unable to write startup pulse to all 12 servos");
    return;
  }

  startupHoming = restored && memcmp(currentPulses, targetPulses, sizeof(currentPulses)) != 0;
  Serial.print("RESET_REASON ");
  Serial.println(resetReasonName());
  Serial.println(startupHoming ? "SERVO_OUTPUTS_HOMING" : "SERVO_OUTPUTS_NEUTRAL");
  imuSetup();  // read only; a missing sensor does not block anything in stage 1
  webSetup();
  // The web task may already be running: read shared state under the mutex.
  xSemaphoreTake(controlMutex, portMAX_DELAY);
  Serial.println("READY SERVO_POSE_V1");
  printStatus();
  xSemaphoreGive(controlMutex);
}

void loop()
{
  // Safety path: never blocked by the web server task (it only holds the mutex
  // for a few microseconds inside a handler).
  if (controlMutex == NULL) {
    delay(100);
    return;
  }
  xSemaphoreTake(controlMutex, portMAX_DELAY);
  readSerialCommands();
  imuUpdate();
  levelUpdate();  // tilt-limit stop, sensor-lost stop, leveling compensation
  updateLease();
  updateWalking();
  updateServoPulses();
  savePoseIfSettled();
  wifiStaService();  // hotspot join/retry; never starts a join while armed
  xSemaphoreGive(controlMutex);
  delay(2);
}
