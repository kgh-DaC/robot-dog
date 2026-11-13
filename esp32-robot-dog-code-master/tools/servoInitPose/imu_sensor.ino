// Tilt sensor (accelerometer) — stage 1: read only.
//
// Supported chips, driven directly by register (no library):
//   MPU family  WHO_AM_I(0x75): 0x71 MPU9250, 0x73 MPU9255, 0x70 MPU6500,
//               0x68 MPU6050, or any other value whose accel data is plausible
//   ICM-20948   WHO_AM_I(0x00) = 0xEA (often sold as "MPU9250", address 0x69)
// A chip is accepted only if it reports a gravity vector of about 1 g.
//
// Nothing here moves a servo. The sketch reports the angles of the sensor's own
// X and Y axes (accelerometer only, low-pass filtered). They become body roll /
// pitch only after the mounting is calibrated with IMUMAP, and the level
// reference with IMUZERO; both are stored in flash ("imuCal" namespace).
//
// Conventions once mapped: roll  > 0 = right side lower,
//                          pitch > 0 = nose (front) lower.
//
// Serial commands (all shared state is touched with controlMutex held, i.e.
// from loop() or a web handler):
//   IMU                  -> IMU ready=.. chip=.. who=.. addr=.. valid=.. age=.. ...
//   IMUZERO              -> store current X/Y as level (robot must be disarmed)
//   IMUMAP <roll> <pitch> e.g. IMUMAP +Y -X   |  IMUMAP CLEAR
//   I2CREAD <addr> <reg> [count]  (hex, diagnostics, read only)

#include <Wire.h>

const uint8_t IMU_SDA_PIN = 21;
const uint8_t IMU_SCL_PIN = 22;
const uint8_t IMU_ADDRESSES[] = {0x69, 0x68};
const uint16_t IMU_SAMPLE_INTERVAL_MS = 20;      // 50 Hz
const uint16_t IMU_STALE_MS = 200;               // older sample = not valid
const float IMU_FILTER_ALPHA = 0.2f;             // EMA weight of a new sample
const float IMU_MIN_G = 0.7f;                    // |a| outside this range: robot
const float IMU_MAX_G = 1.3f;                    // is accelerating, angle unusable
const float IMU_LSB_PER_G = 16384.0f;            // +-2 g range on both families

enum ImuChip { IMU_CHIP_NONE, IMU_CHIP_MPU, IMU_CHIP_ICM20948 };
ImuChip imuChip = IMU_CHIP_NONE;
uint8_t imuDataRegister = 0x3B;                  // ACCEL_XOUT_H

bool imuReady = false;
uint8_t imuAddress = 0;
uint8_t imuWhoAmI = 0;
bool imuHaveSample = false;      // at least one usable sample since boot
bool imuLastSampleOk = false;
unsigned long imuLastReadAt = 0;
unsigned long imuGoodSampleAt = 0;
float imuAx = NAN, imuAy = NAN, imuAz = NAN;   // g, sensor axes
float imuAngleX = NAN, imuAngleY = NAN;        // deg, filtered, before zero offset
float imuZeroX = 0, imuZeroY = 0;
bool imuZeroed = false;
// Mapping: axis 0 = sensor X, 1 = sensor Y; sign +1/-1. Axis -1 = not mapped.
int8_t imuRollAxis = -1, imuRollSign = 1, imuPitchAxis = -1, imuPitchSign = 1;

// Register read with a STOP between the address write and the read (works on
// chips/clones that mishandle a repeated start).
bool readI2cRegisters(uint8_t addr, uint8_t reg, uint8_t *out, uint8_t count)
{
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(true) != 0) return false;
  if (Wire.requestFrom(addr, count) != count) return false;
  for (uint8_t i = 0; i < count; i++) out[i] = Wire.read();
  return true;
}

uint8_t readI2cRegister(uint8_t addr, uint8_t reg, bool &ok)
{
  uint8_t value = 0;
  ok = readI2cRegisters(addr, reg, &value, 1);
  return value;
}

bool writeI2cRegister(uint8_t addr, uint8_t reg, uint8_t value)
{
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission(true) == 0;
}

bool i2cPresent(uint8_t addr)
{
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void printI2cScan()
{
  Serial.print("I2C");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    if (i2cPresent(addr)) {
      Serial.printf(" 0x%02X", addr);
      found++;
    }
  }
  Serial.println(found ? "" : " none");
}

// "I2CREAD 69 75 2" -> "I2CREAD 0x69 0x75 = 71 00"
void printI2cRead(uint8_t addr, uint8_t reg, uint8_t count)
{
  uint8_t data[16];
  if (count < 1 || count > sizeof(data) || !readI2cRegisters(addr, reg, data, count)) {
    Serial.println("ERR i2c_read_failed");
    return;
  }
  Serial.printf("I2CREAD 0x%02X 0x%02X =", addr, reg);
  for (uint8_t i = 0; i < count; i++) Serial.printf(" %02X", data[i]);
  Serial.println();
}

bool readAccelG(float &x, float &y, float &z)
{
  uint8_t raw[6];
  if (!readI2cRegisters(imuAddress, imuDataRegister, raw, 6)) return false;
  x = (int16_t)((raw[0] << 8) | raw[1]) / IMU_LSB_PER_G;
  y = (int16_t)((raw[2] << 8) | raw[3]) / IMU_LSB_PER_G;
  z = (int16_t)((raw[4] << 8) | raw[5]) / IMU_LSB_PER_G;
  return true;
}

const char *imuChipName()
{
  if (imuChip == IMU_CHIP_ICM20948) return "ICM20948";
  if (imuChip != IMU_CHIP_MPU) return "NONE";
  switch (imuWhoAmI) {
    case 0x71: return "MPU9250";
    case 0x73: return "MPU9255";
    case 0x70: return "MPU6500";
    case 0x68: return "MPU6050";
    default: return "MPU_COMPATIBLE";
  }
}

bool initMpuFamily(uint8_t addr)
{
  bool ok = writeI2cRegister(addr, 0x6B, 0x80);  // PWR_MGMT_1: device reset
  delay(100);
  ok = writeI2cRegister(addr, 0x6B, 0x01) && ok; // wake, PLL clock
  delay(10);
  ok = writeI2cRegister(addr, 0x6C, 0x00) && ok; // PWR_MGMT_2: all axes on
  ok = writeI2cRegister(addr, 0x1A, 0x06) && ok; // CONFIG: DLPF (also accel on MPU6050)
  ok = writeI2cRegister(addr, 0x1C, 0x00) && ok; // ACCEL_CONFIG: +-2 g
  if (imuWhoAmI != 0x68) writeI2cRegister(addr, 0x1D, 0x06);  // ACCEL_CONFIG2: DLPF ~5 Hz
  imuDataRegister = 0x3B;
  return ok;
}

bool initIcm20948(uint8_t addr)
{
  bool ok = writeI2cRegister(addr, 0x7F, 0x00);  // REG_BANK_SEL: bank 0
  ok = writeI2cRegister(addr, 0x06, 0x80) && ok; // PWR_MGMT_1: device reset
  delay(100);
  ok = writeI2cRegister(addr, 0x7F, 0x00) && ok;
  ok = writeI2cRegister(addr, 0x06, 0x01) && ok; // wake, auto clock
  delay(10);
  ok = writeI2cRegister(addr, 0x07, 0x00) && ok; // PWR_MGMT_2: accel + gyro on
  ok = writeI2cRegister(addr, 0x7F, 0x20) && ok; // bank 2
  ok = writeI2cRegister(addr, 0x14, 0x31) && ok; // ACCEL_CONFIG: DLPF 6, enabled, +-2 g
  ok = writeI2cRegister(addr, 0x7F, 0x00) && ok; // back to bank 0
  imuDataRegister = 0x2D;                        // ACCEL_XOUT_H (bank 0)
  return ok;
}

// Accept the chip only if it reports roughly 1 g while the robot stands still.
bool imuPlausible()
{
  delay(50);
  for (int attempt = 0; attempt < 5; attempt++) {
    float x, y, z;
    if (readAccelG(x, y, z)) {
      const float magnitude = sqrtf(x * x + y * y + z * z);
      if (magnitude >= IMU_MIN_G && magnitude <= IMU_MAX_G) return true;
    }
    delay(20);
  }
  return false;
}

void loadImuCalibration()
{
  Preferences store;
  if (!store.begin("imuCal", true)) return;
  imuZeroed = store.getBool("zeroed", false);
  imuZeroX = store.getFloat("zx", 0);
  imuZeroY = store.getFloat("zy", 0);
  imuRollAxis = store.getChar("ra", -1);
  imuRollSign = store.getChar("rs", 1);
  imuPitchAxis = store.getChar("pa", -1);
  imuPitchSign = store.getChar("ps", 1);
  store.end();
  const bool mapOk = imuRollAxis >= 0 && imuRollAxis <= 1 && imuPitchAxis >= 0 && imuPitchAxis <= 1 &&
                     imuRollAxis != imuPitchAxis && abs(imuRollSign) == 1 && abs(imuPitchSign) == 1;
  if (!mapOk) imuRollAxis = imuPitchAxis = -1;
  if (!isfinite(imuZeroX) || !isfinite(imuZeroY) || fabsf(imuZeroX) > 45 || fabsf(imuZeroY) > 45) {
    imuZeroed = false;
    imuZeroX = imuZeroY = 0;
  }
}

// Full probe + init of both addresses. Blocks ~200 ms (chip reset delays), so
// after boot it runs only while nothing can move (see imuRecover()).
void imuProbeAndInit()
{
  imuReady = false;
  for (uint8_t i = 0; i < sizeof(IMU_ADDRESSES) && !imuReady; i++) {
    const uint8_t addr = IMU_ADDRESSES[i];
    if (!i2cPresent(addr)) continue;
    bool okMpu = false, okIcm = false;
    const uint8_t whoMpu = readI2cRegister(addr, 0x75, okMpu);
    // Register 0x00 is WHO_AM_I on ICM-20948 (bank 0 after reset); read only.
    const uint8_t whoIcm = readI2cRegister(addr, 0x00, okIcm);
    Serial.printf("IMU_PROBE addr=0x%02X reg75=%s0x%02X reg00=%s0x%02X\n", addr,
                  okMpu ? "" : "fail ", whoMpu, okIcm ? "" : "fail ", whoIcm);
    imuAddress = addr;
    if (okIcm && whoIcm == 0xEA) {
      imuChip = IMU_CHIP_ICM20948;
      imuWhoAmI = whoIcm;
      imuReady = initIcm20948(addr) && imuPlausible();
    } else if (okMpu) {
      imuChip = IMU_CHIP_MPU;
      imuWhoAmI = whoMpu;
      imuReady = initMpuFamily(addr) && imuPlausible();
    }
    if (!imuReady) {
      Serial.printf("IMU_REJECTED addr=0x%02X chip=%s (no plausible 1 g reading)\n", addr, imuChipName());
      imuChip = IMU_CHIP_NONE;
      imuAddress = 0;
    }
  }
  if (imuReady) Serial.printf("IMU_READY chip=%s addr=0x%02X who=0x%02X\n", imuChipName(), imuAddress, imuWhoAmI);
  else Serial.println("IMU_ERROR not_found");
}

void imuSetup()
{
  Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN, 400000);
  Wire.setTimeOut(10);  // ms; a stuck bus must not stall the safety loop
  printI2cScan();
  loadImuCalibration();
  imuProbeAndInit();
}

// "I2CDIAG": read-only wiring diagnostics for a bus where nothing answers.
// Blocks for up to a few seconds, so processCommand() only runs it while
// nothing can move. Reads each line with no pull, with the internal pull-up and
// with the internal pull-down, then scans at 100 kHz and at 400 kHz.
//   line reads 1 even with the pull-down -> an external pull-up is there
//     (module connected and powered)
//   line follows the internal pull (up=1, down=0) -> nothing is connected to it
//   line reads 0 even with the pull-up -> shorted to GND, or the module has no
//     power (its pull-up resistor then pulls the line down)
void i2cDiagScan(uint32_t hz)
{
  Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN, hz);
  Wire.setTimeOut(10);
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    if (i2cPresent(addr)) {
      Serial.printf("%s0x%02X", found ? "," : "", addr);
      found++;
    }
  }
  if (!found) Serial.print("none");
  Wire.end();
}

void printI2cDiag()
{
  Wire.end();
  int level[2][3];
  const uint8_t pins[2] = {IMU_SDA_PIN, IMU_SCL_PIN};
  const uint8_t modes[3] = {INPUT, INPUT_PULLUP, INPUT_PULLDOWN};
  for (int line = 0; line < 2; line++) {
    for (int mode = 0; mode < 3; mode++) {
      pinMode(pins[line], modes[mode]);
      delay(5);
      level[line][mode] = digitalRead(pins[line]);
    }
    pinMode(pins[line], INPUT);
  }
  Serial.printf("I2CDIAG sda=%d/%d/%d scl=%d/%d/%d (none/pullup/pulldown) scan100k=",
                level[0][0], level[0][1], level[0][2], level[1][0], level[1][1], level[1][2]);
  i2cDiagScan(100000);
  Serial.print(" scan400k=");
  i2cDiagScan(400000);
  Serial.println();
  Wire.begin(IMU_SDA_PIN, IMU_SCL_PIN, 400000);  // back to the normal setup
  Wire.setTimeOut(10);
}

// --- Recovery -----------------------------------------------------------------
// Seen on hardware: the module's AD0 pin is not tied, so the chip moved from
// 0x68 (at boot) to 0x69 and every later read failed. Recovery:
// - Quick (no delay, any time): the same chip, already awake and reporting ~1 g,
//   answers at another (or the same) address -> switch to it.
// - Full re-init (~200 ms blocking): only while disarmed, not walking and not
//   homing, e.g. a chip that lost power and woke up in sleep mode, or a sensor
//   that was missing at boot.
const uint16_t IMU_RECOVER_AFTER_MS = 500;      // no good sample this long -> try
const uint16_t IMU_RECOVER_INTERVAL_MS = 1000;
const uint16_t IMU_REPROBE_INTERVAL_MS = 2000;  // sensor not found: full probe interval
unsigned long imuLastRecoverAt = 0;
uint16_t imuRecoverCount = 0;

bool imuSafeToBlock()
{
  return !armed && walkDirection == WALK_STOPPED && !startupHoming;
}

// Awake chip of the current type at addr (usable without a reset).
bool imuAwakeAt(uint8_t addr)
{
  if (!i2cPresent(addr)) return false;
  bool ok = false;
  if (imuChip == IMU_CHIP_ICM20948) {
    if (readI2cRegister(addr, 0x00, ok) != 0xEA || !ok) return false;
    const uint8_t pwr = readI2cRegister(addr, 0x06, ok);  // bank 0 assumed (left there by init)
    if (!ok || (pwr & 0x40)) return false;                 // SLEEP bit: needs a full init
  } else {
    if (readI2cRegister(addr, 0x75, ok) != imuWhoAmI || !ok) return false;
    const uint8_t pwr = readI2cRegister(addr, 0x6B, ok);
    if (!ok || (pwr & 0x40)) return false;
  }
  return true;
}

// Awake chip at addr reporting ~1 g -> switch imuAddress to it.
bool imuQuickSwitch(uint8_t addr)
{
  if (!imuAwakeAt(addr)) return false;
  const uint8_t previous = imuAddress;
  imuAddress = addr;
  float x, y, z;
  if (readAccelG(x, y, z)) {
    const float magnitude = sqrtf(x * x + y * y + z * z);
    if (magnitude >= IMU_MIN_G && magnitude <= IMU_MAX_G) return true;
  }
  imuAddress = previous;
  return false;
}

void imuRecover(unsigned long now)
{
  if (!imuReady) {
    if (now - imuLastRecoverAt < IMU_REPROBE_INTERVAL_MS || !imuSafeToBlock()) return;
    imuLastRecoverAt = now;
    if (!i2cPresent(0x68) && !i2cPresent(0x69)) return;  // nothing there: stay quiet
    imuProbeAndInit();
    if (imuReady) imuRecoverCount++;
    return;
  }
  if (now - imuLastRecoverAt < IMU_RECOVER_INTERVAL_MS) return;
  const unsigned long lastGood = imuHaveSample ? imuGoodSampleAt : 0;
  if (now - lastGood < IMU_RECOVER_AFTER_MS) return;
  imuLastRecoverAt = now;
  const uint8_t previous = imuAddress;
  for (uint8_t i = 0; i < sizeof(IMU_ADDRESSES); i++) {
    if (imuQuickSwitch(IMU_ADDRESSES[i])) {
      if (imuAddress != previous) {
        imuRecoverCount++;
        Serial.printf("IMU_RECOVERED addr=0x%02X (was 0x%02X)\n", imuAddress, previous);
      }
      return;  // same address and plausible: just a disturbance, keep going
    }
  }
  // Chip still awake where it was, only the readings are implausible (robot
  // shaken or carried): not a connection problem, do not reset it.
  if (previous != 0 && imuAwakeAt(previous)) return;
  if (!imuSafeToBlock()) return;  // invalid until disarmed; leveling stops on imu_lost
  imuProbeAndInit();
  if (imuReady) {
    imuRecoverCount++;
    Serial.printf("IMU_RECOVERED addr=0x%02X (reinit)\n", imuAddress);
  }
}

// Called from loop() with controlMutex held.
void imuUpdate()
{
  const unsigned long now = millis();
  imuRecover(now);
  if (!imuReady) return;
  if (now - imuLastReadAt < IMU_SAMPLE_INTERVAL_MS) return;
  imuLastReadAt = now;

  struct { float x, y, z; } g;
  if (!readAccelG(g.x, g.y, g.z)) {
    imuLastSampleOk = false;
    return;
  }
  const float magnitude = sqrtf(g.x * g.x + g.y * g.y + g.z * g.z);
  imuLastSampleOk = isfinite(magnitude) && magnitude >= IMU_MIN_G && magnitude <= IMU_MAX_G;
  if (!imuLastSampleOk) return;

  imuAx = g.x;
  imuAy = g.y;
  imuAz = g.z;
  // Tilt of each sensor axis from gravity (deg). Valid while |a| ~ 1 g.
  const float angleX = atan2f(g.y, g.z) * 180.0f / M_PI;                          // about X
  const float angleY = atan2f(-g.x, sqrtf(g.y * g.y + g.z * g.z)) * 180.0f / M_PI;  // about Y
  if (!imuHaveSample || now - imuGoodSampleAt > IMU_STALE_MS) {
    imuAngleX = angleX;
    imuAngleY = angleY;
  } else {
    imuAngleX += IMU_FILTER_ALPHA * (angleX - imuAngleX);
    imuAngleY += IMU_FILTER_ALPHA * (angleY - imuAngleY);
  }
  imuHaveSample = true;
  imuGoodSampleAt = now;
}

bool imuValid()
{
  return imuReady && imuHaveSample && millis() - imuGoodSampleAt <= IMU_STALE_MS;
}

long imuAgeMs()
{
  return imuHaveSample ? (long)(millis() - imuGoodSampleAt) : -1;
}

float imuAxisAngle(int8_t axis)
{
  return axis == 0 ? imuAngleX - imuZeroX : imuAngleY - imuZeroY;
}

bool imuMapped()
{
  return imuRollAxis >= 0 && imuPitchAxis >= 0;
}

float imuRoll()
{
  return imuValid() && imuMapped() ? imuRollSign * imuAxisAngle(imuRollAxis) : NAN;
}

float imuPitch()
{
  return imuValid() && imuMapped() ? imuPitchSign * imuAxisAngle(imuPitchAxis) : NAN;
}

void formatImuAxis(char *out, size_t size, int8_t axis, int8_t sign)
{
  if (axis < 0) snprintf(out, size, "NONE");
  else snprintf(out, size, "%c%c", sign > 0 ? '+' : '-', axis == 0 ? 'X' : 'Y');
}

// Writes "nan"-free numbers: unknown values become the given placeholder.
void formatImuFloat(char *out, size_t size, float value, const char *unknown)
{
  if (isfinite(value)) snprintf(out, size, "%.2f", value);
  else snprintf(out, size, "%s", unknown);
}

void printImu()
{
  const bool valid = imuValid();
  char ax[12], ay[12], az[12], x[12], y[12], roll[12], pitch[12], rollMap[6], pitchMap[6];
  formatImuFloat(ax, sizeof(ax), valid ? imuAx : NAN, "-");
  formatImuFloat(ay, sizeof(ay), valid ? imuAy : NAN, "-");
  formatImuFloat(az, sizeof(az), valid ? imuAz : NAN, "-");
  formatImuFloat(x, sizeof(x), valid ? imuAxisAngle(0) : NAN, "-");
  formatImuFloat(y, sizeof(y), valid ? imuAxisAngle(1) : NAN, "-");
  formatImuFloat(roll, sizeof(roll), imuRoll(), "-");
  formatImuFloat(pitch, sizeof(pitch), imuPitch(), "-");
  formatImuAxis(rollMap, sizeof(rollMap), imuRollAxis, imuRollSign);
  formatImuAxis(pitchMap, sizeof(pitchMap), imuPitchAxis, imuPitchSign);
  Serial.printf("IMU ready=%d chip=%s who=0x%02X addr=0x%02X valid=%d age=%ld ax=%s ay=%s az=%s x=%s y=%s "
                "zeroed=%d rollmap=%s pitchmap=%s roll=%s pitch=%s recover=%u\n",
                imuReady ? 1 : 0, imuChipName(), imuWhoAmI, imuAddress, valid ? 1 : 0, imuAgeMs(), ax, ay, az, x, y,
                imuZeroed ? 1 : 0, rollMap, pitchMap, roll, pitch, (unsigned)imuRecoverCount);
}

// JSON object for /api/status (same fields as the serial IMU line).
void formatImuJson(char *out, size_t size)
{
  const bool valid = imuValid();
  char ax[12], ay[12], az[12], x[12], y[12], roll[12], pitch[12], rollMap[6], pitchMap[6];
  formatImuFloat(ax, sizeof(ax), valid ? imuAx : NAN, "null");
  formatImuFloat(ay, sizeof(ay), valid ? imuAy : NAN, "null");
  formatImuFloat(az, sizeof(az), valid ? imuAz : NAN, "null");
  formatImuFloat(x, sizeof(x), valid ? imuAxisAngle(0) : NAN, "null");
  formatImuFloat(y, sizeof(y), valid ? imuAxisAngle(1) : NAN, "null");
  formatImuFloat(roll, sizeof(roll), imuRoll(), "null");
  formatImuFloat(pitch, sizeof(pitch), imuPitch(), "null");
  formatImuAxis(rollMap, sizeof(rollMap), imuRollAxis, imuRollSign);
  formatImuAxis(pitchMap, sizeof(pitchMap), imuPitchAxis, imuPitchSign);
  snprintf(out, size,
           "{\"ready\":%s,\"chip\":\"%s\",\"whoAmI\":\"0x%02X\",\"address\":\"0x%02X\",\"valid\":%s,\"ageMs\":%ld,"
           "\"accG\":{\"x\":%s,\"y\":%s,\"z\":%s},\"axisDeg\":{\"x\":%s,\"y\":%s},\"zeroed\":%s,"
           "\"rollMap\":\"%s\",\"pitchMap\":\"%s\",\"rollDeg\":%s,\"pitchDeg\":%s}",
           imuReady ? "true" : "false", imuChipName(), imuWhoAmI, imuAddress, valid ? "true" : "false", imuAgeMs(),
           ax, ay, az, x, y, imuZeroed ? "true" : "false", rollMap, pitchMap, roll, pitch);
}

// Calibration changes are refused while anything may move.
const char *imuCalibrationBlocked()
{
  if (armed || walkDirection != WALK_STOPPED) return "walking_active";
  if (!imuReady) return "imu_not_ready";
  return NULL;
}

const char *imuZero()
{
  const char *blocked = imuCalibrationBlocked();
  if (blocked != NULL) return blocked;
  if (!imuValid()) return "imu_not_valid";
  Preferences store;
  if (!store.begin("imuCal", false)) return "flash_error";
  store.putFloat("zx", imuAngleX);
  store.putFloat("zy", imuAngleY);
  store.putBool("zeroed", true);
  store.end();
  imuZeroX = imuAngleX;
  imuZeroY = imuAngleY;
  imuZeroed = true;
  return NULL;
}

bool parseImuAxis(const char *text, int8_t &axis, int8_t &sign)
{
  if (text == NULL || strlen(text) != 2 || (text[0] != '+' && text[0] != '-')) return false;
  if (text[1] != 'X' && text[1] != 'Y') return false;
  sign = text[0] == '+' ? 1 : -1;
  axis = text[1] == 'X' ? 0 : 1;
  return true;
}

// rollText/pitchText like "+Y" / "-X"; rollText "CLEAR" removes the mapping.
const char *imuSetMap(const char *rollText, const char *pitchText)
{
  const char *blocked = imuCalibrationBlocked();
  if (blocked != NULL) return blocked;
  int8_t ra = -1, rs = 1, pa = -1, ps = 1;
  const bool clear = rollText != NULL && strcmp(rollText, "CLEAR") == 0 && pitchText == NULL;
  if (!clear && (!parseImuAxis(rollText, ra, rs) || !parseImuAxis(pitchText, pa, ps) || ra == pa)) {
    return "invalid_imu_map";
  }
  Preferences store;
  if (!store.begin("imuCal", false)) return "flash_error";
  store.putChar("ra", ra);
  store.putChar("rs", rs);
  store.putChar("pa", pa);
  store.putChar("ps", ps);
  store.end();
  imuRollAxis = ra;
  imuRollSign = rs;
  imuPitchAxis = pa;
  imuPitchSign = ps;
  return NULL;
}
