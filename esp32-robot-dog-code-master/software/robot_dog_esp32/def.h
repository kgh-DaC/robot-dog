/**
 * Some usuful defines
 */

#define DISPLAY_DIGITS 1  // Number of digits to display after the decimal point


#define PCA9685  100
#define MPU9650  101
#define SD1306   102
#define ESP32PWM 103
#define INA219   104

/**
 * Axis
 */
#define ROLL  0
#define PITCH 1
#define YAW   2

#define SOFTWARE_CORE_VERSION 1

/* Math */
#define RADTODEG 180/PI
#define DEGTORAD PI/180

/* WiFi */
#define AP_MODE 0

/* Packages code */
#define P_TELEMETRY_V1_LEN 14
#define P_TELEMETRY_LEN 40
#define P_TELEMETRY_VERSION 2
#define P_TELEMETRY_STATUS_OFFSET 2
#define P_TELEMETRY_STATUS_IMU_READY 0x0001
#define P_TELEMETRY_STATUS_IMU_VALID 0x0002
#define P_TELEMETRY_STATUS_POWER_READY 0x0004
#define P_TELEMETRY_STATUS_POWER_VALID 0x0008
#define P_TELEMETRY_STATUS_ARMED 0x0010
#define P_TELEMETRY_IMU_OFFSET 4
#define P_TELEMETRY_JOINTS_OFFSET 10
#define P_TELEMETRY_VOLTAGE_OFFSET 34
#define P_TELEMETRY_CURRENT_OFFSET 36
#define P_TELEMETRY_LOOP_TIME_OFFSET 38

#define P_MOVE      77  // ASCII "M"
#define P_TELEMETRY 84  //       "T"
#define P_ARM       65  // ASCII "A"
#define P_STOP      83  // ASCII "S"
#define P_TELEMETRY_JOINT_COUNT 12

/* Debug (SLOW!!!)*/
//#define DEBUG_HAL_LEG

typedef struct {
  uint8_t minAngle; // minimal servo angle, deg
  uint8_t maxAngle; // maximal servo angle, deg
  uint16_t degMin;  // PWM pulse at minAngle deg
  uint16_t deg30;   // PWM pulse at 30 deg
  uint16_t deg50;
  uint16_t deg70;
  uint16_t deg90;   // PWM pusle at 90 deg
  uint16_t deg110;
  uint16_t deg130;
  uint16_t deg150;  // PWM pulse at 150 deg
  uint16_t degMax;  // PWM pusle at maxAngle deg
} servoProfile;
