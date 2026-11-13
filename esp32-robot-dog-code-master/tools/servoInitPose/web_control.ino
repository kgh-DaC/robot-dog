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
//
// Hotspot (station) mode: the AP above always stays on. If a hotspot was saved
// with the serial command "WIFI_SET <ssid><TAB><password>" (stored in flash,
// never printed or sent back), the ESP32 also joins that network and serves
// the same page at its DHCP address and at http://robotdog.local. A failed
// join gives up after STA_TRY_MS so channel scanning does not disturb the AP,
// and is retried every STA_RETRY_MS only while the robot is not armed.
// "WIFI_CLEAR" forgets the hotspot.

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
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

// ---- hotspot (station) link ------------------------------------------------
const char MDNS_NAME[] = "robotdog";            // http://robotdog.local
const uint32_t STA_TRY_MS = 15000;              // one join attempt
const uint32_t STA_RETRY_MS = 30000;            // pause before the next attempt
// Origins allowed to call the API from another page (the USB gateway page
// switching to the hotspot link). Only loopback: any other site stays blocked.
const char *const CORS_ORIGINS[] = {"http://127.0.0.1:8787", "http://localhost:8787"};

enum StaState { STA_OFF, STA_CONNECTING, STA_CONNECTED, STA_NOT_FOUND, STA_FAILED };
char staSsid[33] = "";
char staPass[64] = "";
volatile StaState staState = STA_OFF;
bool staTrying = false;
uint32_t staAttemptMs = 0;
uint32_t staNextMs = 0;
char staIp[16] = "";                             // written in loop(), read by the web task
uint32_t staIpRaw = 0;                           // staIp as a number, to notice a changed address

const char *staStateName(int value)  // int: Arduino prototypes precede the enum
{
  switch (value) {
    case STA_CONNECTING: return "CONNECTING";
    case STA_CONNECTED: return "CONNECTED";
    case STA_NOT_FOUND: return "NOT_FOUND";
    case STA_FAILED: return "FAILED";
    default: return "OFF";
  }
}

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
  Serial.print(WiFi.softAPgetStationNum());
  // sta_ssid goes last: it may contain spaces (the rest of the line is the SSID).
  Serial.print(" sta=");
  Serial.print(staStateName(staState));
  Serial.print(" sta_ip=");
  Serial.print(staIp[0] ? staIp : "-");
  Serial.print(" sta_ssid=");
  Serial.println(staSsid);
}

// ---- hotspot (station) control ------------------------------------------------

void loadStaConfig()
{
  Preferences store;
  if (!store.begin("wifiSta", true)) return;
  String ssid = store.getString("ssid", "");
  String pass = store.getString("pass", "");
  store.end();
  if (ssid.length() == 0 || ssid.length() >= sizeof(staSsid) || pass.length() >= sizeof(staPass)) return;
  strcpy(staSsid, ssid.c_str());
  strcpy(staPass, pass.c_str());
}

void staBegin(uint32_t now)
{
  WiFi.begin(staSsid, staPass[0] ? staPass : NULL);
  staTrying = true;
  staAttemptMs = now;
  staState = STA_CONNECTING;
}

void staStop()
{
  WiFi.disconnect(false, true);  // keep the radio (AP) on, forget the STA config
  staTrying = false;
  staIp[0] = '\0';
  staIpRaw = 0;
}

// Called from loop() with controlMutex held. Gives up a failed join after
// STA_TRY_MS and retries only while disarmed, so scanning never disturbs a
// controller connected through the AP while the robot can move.
void wifiStaService()
{
  if (!wifiReady || staSsid[0] == '\0') return;
  const uint32_t now = millis();
  const wl_status_t status = WiFi.status();
  if (status == WL_CONNECTED) {
    // Also catches a new address after a DHCP renewal (Host check and status follow it).
    const uint32_t ipNow = (uint32_t)WiFi.localIP();
    if (staState != STA_CONNECTED || ipNow != staIpRaw) {
      WiFi.localIP().toString().toCharArray(staIp, sizeof(staIp));
      staIpRaw = ipNow;
      staState = STA_CONNECTED;
      staTrying = false;
      Serial.print("EVENT WIFI_STA connected ip=");
      Serial.println(staIp);
    }
    return;
  }
  if (armed && staTrying) {         // armed during a join: stop scanning now, retry after STOP
    staStop();
    staState = STA_FAILED;
    staNextMs = now;
    Serial.println("EVENT WIFI_STA FAILED armed");
    return;
  }
  if (staState == STA_CONNECTED) {  // hotspot went away
    staIp[0] = '\0';
    staIpRaw = 0;
    staState = STA_FAILED;
    staNextMs = now;                // retry at once (subject to the armed check)
    Serial.println("EVENT WIFI_STA lost");
  }
  if (staTrying) {
    if (now - staAttemptMs < STA_TRY_MS) return;
    staState = status == WL_NO_SSID_AVAIL ? STA_NOT_FOUND : STA_FAILED;
    staStop();
    staNextMs = now + STA_RETRY_MS;
    Serial.print("EVENT WIFI_STA ");
    Serial.println(staStateName(staState));
    return;
  }
  if (!armed && (int32_t)(now - staNextMs) >= 0) staBegin(now);
}

// Serial only (USB = physical access). Caller holds controlMutex (loop()).
// ssid: 1..32 bytes; pass: "" (open network) or 8..63 printable ASCII.
const char *wifiStaSet(const char *ssid, const char *pass)
{
  if (!wifiReady) return "wifi_disabled";
  if (armed) return "armed";
  const size_t ssidLen = strlen(ssid), passLen = strlen(pass);
  if (ssidLen == 0 || ssidLen >= sizeof(staSsid)) return "invalid_ssid";
  for (size_t i = 0; i < ssidLen; i++) {
    if ((unsigned char)ssid[i] < 0x20 || ssid[i] == 0x7f) return "invalid_ssid";
  }
  if (passLen != 0 && (passLen < 8 || passLen >= sizeof(staPass))) return "invalid_password";
  for (size_t i = 0; i < passLen; i++) {
    if (pass[i] < 0x20 || pass[i] > 0x7e) return "invalid_password";
  }
  Preferences store;
  if (!store.begin("wifiSta", false)) return "store_failed";
  const bool saved = store.putString("ssid", ssid) == ssidLen && store.putString("pass", pass) == passLen;
  store.end();
  if (!saved) return "store_failed";
  staStop();
  strcpy(staSsid, ssid);
  strcpy(staPass, pass);
  staBegin(millis());
  return NULL;
}

const char *wifiStaClear()
{
  if (!wifiReady) return "wifi_disabled";
  if (armed) return "armed";
  Preferences store;
  if (store.begin("wifiSta", false)) {
    store.clear();
    store.end();
  }
  staStop();
  staSsid[0] = '\0';
  staPass[0] = '\0';
  staState = STA_OFF;
  return NULL;
}

// JSON string body (no quotes): escapes '"', '\\' and control characters.
void jsonEscape(char *out, size_t size, const char *text)
{
  size_t n = 0;
  for (const char *p = text; *p && n + 7 < size; p++) {
    const unsigned char c = (unsigned char)*p;
    if (c == '"' || c == '\\') {
      out[n++] = '\\';
      out[n++] = c;
    } else if (c < 0x20) {
      n += snprintf(out + n, size - n, "\\u%04x", c);
    } else {
      out[n++] = c;
    }
  }
  out[n] = '\0';
}

// {"status":"CONNECTED","ip":"172.20.10.3","ssid":"..."}; never the password.
void formatStaJson(char *out, size_t size)
{
  char ssid[200];  // 32 bytes, worst case 6x when every byte is a control escape
  jsonEscape(ssid, sizeof(ssid), staSsid);
  snprintf(out, size, "{\"status\":\"%s\",\"ip\":\"%s\",\"ssid\":\"%s\",\"host\":\"%s.local\"}",
           staStateName(staState), staIp, ssid, MDNS_NAME);
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

bool corsOriginAllowed()
{
  const String origin = webServer.header("Origin");
  for (const char *allowed : CORS_ORIGINS) {
    if (origin == allowed) return true;
  }
  return false;
}

void sendNoStoreHeaders()
{
  webServer.sendHeader("Cache-Control", "no-store");
  webServer.sendHeader("X-Content-Type-Options", "nosniff");
  if (corsOriginAllowed()) {
    webServer.sendHeader("Access-Control-Allow-Origin", webServer.header("Origin"));
    webServer.sendHeader("Vary", "Origin");
  }
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

// Blocks DNS rebinding: a foreign web page whose host name resolves to one of
// the robot's addresses still sends its own name in the Host header.
// Accepted: the AP address, the current hotspot address and robotdog.local.
bool hostOk()
{
  String host = webServer.hostHeader();
  if (host.endsWith(":80")) host.remove(host.length() - 3);
  host.toLowerCase();
  char ip[sizeof(staIp)];
  {
    ControlLock lock;
    strcpy(ip, staIp);
  }
  if (host == "192.168.4.1" || (ip[0] && host == ip) || host == String(MDNS_NAME) + ".local") return true;
  sendJsonResult(403, "bad_host");
  return false;
}

// CORS preflight for the API. Approved only for CORS_ORIGINS (the USB gateway
// page on this computer); every other site keeps getting a refusal, so the
// X-Robot-Control header still blocks cross-site commands.
void handlePreflight()
{
  if (!hostOk()) return;
  if (!corsOriginAllowed()) {
    sendJsonResult(403, "origin_not_allowed");
    return;
  }
  sendNoStoreHeaders();
  webServer.sendHeader("Access-Control-Allow-Methods", "GET, POST");
  webServer.sendHeader("Access-Control-Allow-Headers", "Content-Type, X-Robot-Control");
  if (webServer.header("Access-Control-Request-Private-Network") == "true") {
    webServer.sendHeader("Access-Control-Allow-Private-Network", "true");
  }
  webServer.sendHeader("Access-Control-Max-Age", "600");
  webServer.send(204);
}

void handleStatus()
{
  if (!hostOk()) return;
  const String session = webServer.arg("session");
  const bool sessionValid = validSession(session);
  const int clients = WiFi.softAPgetStationNum();  // outside the lock
  char body[1792];  // worst case ~1.5 kB with the IMU, level, standTrimDeg and sta objects
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
    char sta[320];  // worst case ~290 B (escaped 32-byte SSID)
    formatStaJson(sta, sizeof(sta));
    char trim[96];  // worst case 58 B: {"LF":-99.9,"RF":-99.9,"LH":-99.9,"RH":-99.9}
    snprintf(trim, sizeof(trim), "{\"LF\":%.1f,\"RF\":%.1f,\"LH\":%.1f,\"RH\":%.1f}",
             STAND_GAMMA_TRIM_DEG[0], STAND_GAMMA_TRIM_DEG[1], STAND_GAMMA_TRIM_DEG[2],
             STAND_GAMMA_TRIM_DEG[3]);
    snprintf(body, sizeof(body),
             "{\"link\":\"wifi\",\"connected\":true,\"armed\":%s,\"controller\":\"%s\","
             "\"youControl\":%s,\"walk\":\"%s\",\"homing\":%s,\"settled\":%s,"
             "\"resetReason\":\"%s\",\"lastStop\":\"%s\",\"uptimeMs\":%lu,"
             "\"leaseTimeoutMs\":%u,\"angles\":%s,\"wifi\":{\"ssid\":\"%s\",\"clients\":%d,\"sta\":%s},\"imu\":%s,\"level\":%s,\"standTrimDeg\":%s}",
             armed ? "true" : "false", controllerName(controller),
             youControl ? "true" : "false", walkName(walkDirection),
             startupHoming ? "true" : "false", poseSettledAndSaved() ? "true" : "false",
             resetReasonName(), lastStopReason, (unsigned long)millis(),
             (unsigned)LEASE_TIMEOUT_MS, angles, wifiSsid, clients, sta, imuJson, levelJson, trim);
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
  // A custom header forces a CORS preflight, which this server approves only for
  // CORS_ORIGINS, so other web sites cannot send commands through the user's browser.
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
  // Station config lives only in our own "wifiSta" store; the driver must not
  // reconnect on its own (wifiStaService decides when to try).
  WiFi.persistent(false);
  WiFi.setAutoReconnect(false);
  WiFi.mode(WIFI_AP_STA);
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

  const char *headers[] = {"X-Robot-Control", "Origin", "Access-Control-Request-Private-Network"};
  webServer.collectHeaders(headers, 3);
  webServer.on("/", HTTP_GET, handleRoot);
  webServer.on("/api/status", HTTP_GET, handleStatus);
  webServer.on("/api/command", HTTP_POST, handleCommand);
  webServer.on("/api/status", HTTP_OPTIONS, handlePreflight);
  webServer.on("/api/command", HTTP_OPTIONS, handlePreflight);
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
  if (MDNS.begin(MDNS_NAME)) MDNS.addService("http", "tcp", 80);
  else Serial.println("WIFI_ERROR mdns_failed");
  loadStaConfig();
  if (staSsid[0]) staBegin(millis());
  printWifiInfo();
}
