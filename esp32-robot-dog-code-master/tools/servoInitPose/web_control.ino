// Wi-Fi access point + HTTP control page for the robot dog.
//
// The ESP32 creates its own WPA2 network (no internet, no router needed).
// SSID is "RobotDog-XXXX"; the password is WIFI_FIXED_PASSWORD, or if that is
// empty a random one generated on first boot and stored in flash. It is
// printed over USB serial ("WIFI" command / boot log).
// Browse to http://192.168.4.1 after joining the network.
//
// The page and API are identical to the USB gateway (software/usb_gateway),
// so the same web page works over both links. Safety rules live in the main
// sketch (controlArm/controlWalk/controlHeartbeat/controlStop): explicit ARM,
// 1 s heartbeat lease, single controller, STOP always accepted.
//
// The server runs in its own task (webTask) so a slow or stalled HTTP client
// cannot delay the lease check, serial STOP or servo slewing in loop().
// Handlers take controlMutex only around shared state, never around network I/O.

#include <WiFi.h>
#include <WebServer.h>
#include <esp_mac.h>
#include "web_page.h"

const bool WIFI_ENABLED = true;
const uint8_t WIFI_CHANNEL = 6;
const uint8_t WIFI_MAX_CLIENTS = 2;
const IPAddress WIFI_AP_IP(192, 168, 4, 1);
const uint32_t WEB_TASK_STACK = 8192;
const UBaseType_t WEB_TASK_PRIORITY = 1;
const BaseType_t WEB_TASK_CORE = 0;  // loop() runs on core 1

WebServer webServer(80);
bool wifiReady = false;
char wifiSsid[24] = "";
char wifiPass[16] = "";
char wifiOwnerSession[33] = "";

// Fixed password chosen by the user (WPA2 needs 8..63 characters). Anyone who
// knows it and is in range can open the control page. Set to "" to go back to
// the random per-board password stored in flash.
const char WIFI_FIXED_PASSWORD[] = "12345678";

void loadOrCreateWifiPassword()
{
  if (strlen(WIFI_FIXED_PASSWORD) >= 8 && strlen(WIFI_FIXED_PASSWORD) < sizeof(wifiPass)) {
    strcpy(wifiPass, WIFI_FIXED_PASSWORD);
    return;
  }
  Preferences wifiStore;
  const bool storeReady = wifiStore.begin("wifiAp", false);
  if (storeReady) {
    String stored = wifiStore.getString("pass", "");
    if (stored.length() >= 8 && stored.length() < sizeof(wifiPass)) {
      strcpy(wifiPass, stored.c_str());
      wifiStore.end();
      return;
    }
  }
  // esp_random() is a hardware RNG once the radio is running (WiFi.mode called).
  const char alphabet[] = "abcdefghjkmnpqrstuvwxyz23456789";
  for (int i = 0; i < 10; i++) {
    wifiPass[i] = alphabet[esp_random() % (sizeof(alphabet) - 1)];
  }
  wifiPass[10] = '\0';
  if (storeReady) {
    wifiStore.putString("pass", wifiPass);
    wifiStore.end();
  }
}

void printWifiInfo()
{
  if (!wifiReady) {
    Serial.println("WIFI disabled");
    return;
  }
  Serial.print("WIFI ssid=");
  Serial.print(wifiSsid);
  Serial.print(" pass=");
  Serial.print(wifiPass);
  Serial.print(" ip=");
  Serial.print(WiFi.softAPIP());
  Serial.print(" clients=");
  Serial.println(WiFi.softAPgetStationNum());
}

bool validSession(const String &session)
{
  if (session.length() < 16 || session.length() >= sizeof(wifiOwnerSession)) return false;
  for (size_t i = 0; i < session.length(); i++) {
    if (!isalnum((unsigned char)session[i])) return false;
  }
  return true;
}

bool sessionOwnsControl(const String &session)
{
  return armed && controller == CTRL_WIFI && strcmp(wifiOwnerSession, session.c_str()) == 0;
}

// RAII lock for controlMutex (shared with loop() in servoInitPose.ino).
struct ControlLock {
  ControlLock() { xSemaphoreTake(controlMutex, portMAX_DELAY); }
  ~ControlLock() { xSemaphoreGive(controlMutex); }
};

void sendNoStoreHeaders()
{
  webServer.sendHeader("Cache-Control", "no-store");
  webServer.sendHeader("X-Content-Type-Options", "nosniff");
}

void sendJsonResult(int status, const char *error)
{
  sendNoStoreHeaders();
  if (error == NULL) {
    webServer.send(status, "application/json", "{\"ok\":true}");
    return;
  }
  char body[96];
  snprintf(body, sizeof(body), "{\"ok\":false,\"error\":\"%s\"}", error);
  webServer.send(status, "application/json", body);
}

void handleRoot()
{
  sendNoStoreHeaders();
  webServer.send(200, "text/html; charset=utf-8", ROBOT_CONTROL_PAGE);
}

// Blocks DNS rebinding: a foreign web page whose host name resolves to
// 192.168.4.1 still sends its own name in the Host header.
bool hostOk()
{
  const String host = webServer.hostHeader();
  if (host == "192.168.4.1" || host == "192.168.4.1:80") return true;
  sendJsonResult(403, "bad_host");
  return false;
}

void handleStatus()
{
  if (!hostOk()) return;
  const String session = webServer.arg("session");
  const bool sessionValid = validSession(session);
  const int clients = WiFi.softAPgetStationNum();  // outside the lock
  char body[1536];  // worst case ~1.1 kB with the IMU and level objects
  {
    ControlLock lock;
    const bool youControl = sessionValid && sessionOwnsControl(session);
    char imuJson[360];
    formatImuJson(imuJson, sizeof(imuJson));
    char levelJson[320];
    formatLevelJson(levelJson, sizeof(levelJson));
    char angles[160];
    snprintf(angles, sizeof(angles),
             "{\"LF\":[%d,%d,%d],\"RF\":[%d,%d,%d],\"LH\":[%d,%d,%d],\"RH\":[%d,%d,%d]}",
             servos[0].targetAngle, servos[1].targetAngle, servos[2].targetAngle,
             servos[3].targetAngle, servos[4].targetAngle, servos[5].targetAngle,
             servos[6].targetAngle, servos[7].targetAngle, servos[8].targetAngle,
             servos[9].targetAngle, servos[10].targetAngle, servos[11].targetAngle);
    snprintf(body, sizeof(body),
             "{\"link\":\"wifi\",\"connected\":true,\"armed\":%s,\"controller\":\"%s\","
             "\"youControl\":%s,\"walk\":\"%s\",\"homing\":%s,\"settled\":%s,"
             "\"resetReason\":\"%s\",\"lastStop\":\"%s\",\"uptimeMs\":%lu,"
             "\"leaseTimeoutMs\":%u,\"angles\":%s,\"wifi\":{\"ssid\":\"%s\",\"clients\":%d},\"imu\":%s,\"level\":%s}",
             armed ? "true" : "false", controllerName(controller),
             youControl ? "true" : "false", walkName(walkDirection),
             startupHoming ? "true" : "false", poseSettledAndSaved() ? "true" : "false",
             resetReasonName(), lastStopReason, (unsigned long)millis(),
             (unsigned)LEASE_TIMEOUT_MS, angles, wifiSsid, clients, imuJson, levelJson);
  }
  sendNoStoreHeaders();
  webServer.send(200, "application/json", body);
}

// Applies one command to the shared control state. Caller holds controlMutex.
// Returns NULL on success or an error code; *status is the HTTP status.
const char *applyCommand(const String &cmd, const String &session, const String &dirText, int *status)
{
  *status = 409;
  if (cmd == "STOP") {
    controlStop("wifi_stop");
    return NULL;
  }
  if (!validSession(session)) {
    *status = 400;
    return "invalid_session";
  }
  if (cmd == "ARM") {
    if (armed && controller == CTRL_WIFI && !sessionOwnsControl(session)) return "controlled_by_other";
    const char *error = controlArm(CTRL_WIFI);
    if (error == NULL) strcpy(wifiOwnerSession, session.c_str());
    return error;
  }
  // Tilt sensor calibration: no motion, refused while anyone is armed/walking.
  if (cmd == "IMUZERO") return imuZero();
  if (cmd == "IMUMAP") {
    const String roll = webServer.arg("roll");
    const String pitch = webServer.arg("pitch");
    const char *error = roll == "CLEAR" ? imuSetMap("CLEAR", NULL) : imuSetMap(roll.c_str(), pitch.c_str());
    if (error != NULL && strcmp(error, "invalid_imu_map") == 0) *status = 400;
    return error;
  }
  if (cmd == "LEVEL") {  // on=1|0; while armed only the controlling page may change it
    const String on = webServer.arg("on");
    if (on != "1" && on != "0") {
      *status = 400;
      return "invalid_level";
    }
    return levelSet(on == "1", !armed || sessionOwnsControl(session));
  }
  if (!sessionOwnsControl(session)) return "not_armed";
  if (cmd == "HB") return controlHeartbeat(CTRL_WIFI);
  if (cmd == "DISARM") {
    controlStop("disarm");
    return NULL;
  }
  if (cmd == "WALK") {
    WalkDirection direction = WALK_STOPPED;
    if (!parseWalkDirection(dirText.c_str(), direction)) {
      *status = 400;
      return "invalid_direction";
    }
    return controlWalk(CTRL_WIFI, direction);
  }
  *status = 400;
  return "unknown_command";
}

void handleCommand()
{
  if (!hostOk()) return;
  // A custom header forces a CORS preflight, which this server never approves,
  // so other web sites cannot send commands through the user's browser.
  if (webServer.header("X-Robot-Control") != "1") {
    sendJsonResult(403, "missing_control_header");
    return;
  }
  const String cmd = webServer.arg("cmd");
  const String session = webServer.arg("session");
  const String dirText = webServer.arg("dir");
  int status = 409;
  const char *error = NULL;  // always a string literal, safe after unlock
  {
    ControlLock lock;
    error = applyCommand(cmd, session, dirText, &status);
  }
  sendJsonResult(error == NULL ? 200 : status, error);
}

void webTask(void *)
{
  for (;;) {
    webServer.handleClient();
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

void webSetup()
{
  if (!WIFI_ENABLED) return;
  WiFi.mode(WIFI_AP);
  loadOrCreateWifiPassword();
  uint8_t mac[6];
  // From eFuse: valid even before the Wi-Fi driver has started.
  if (esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP) != ESP_OK) memset(mac, 0, sizeof(mac));
  snprintf(wifiSsid, sizeof(wifiSsid), "RobotDog-%02X%02X", mac[4], mac[5]);
  WiFi.softAPConfig(WIFI_AP_IP, WIFI_AP_IP, IPAddress(255, 255, 255, 0));
  if (!WiFi.softAP(wifiSsid, wifiPass, WIFI_CHANNEL, 0, WIFI_MAX_CLIENTS)) {
    Serial.println("WIFI_ERROR softap_failed");
    WiFi.mode(WIFI_OFF);
    return;
  }
  // Lower TX power: less current draw on the shared servo/ESP32 battery.
  WiFi.setTxPower(WIFI_POWER_8_5dBm);

  const char *headers[] = {"X-Robot-Control"};
  webServer.collectHeaders(headers, 1);
  webServer.on("/", HTTP_GET, handleRoot);
  webServer.on("/api/status", HTTP_GET, handleStatus);
  webServer.on("/api/command", HTTP_POST, handleCommand);
  webServer.onNotFound([]() { sendJsonResult(404, "not_found"); });
  webServer.begin();
  if (xTaskCreatePinnedToCore(webTask, "web", WEB_TASK_STACK, NULL, WEB_TASK_PRIORITY,
                              NULL, WEB_TASK_CORE) != pdPASS) {
    Serial.println("WIFI_ERROR web_task_failed");
    webServer.stop();
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_OFF);
    return;
  }
  wifiReady = true;
  printWifiInfo();
}
