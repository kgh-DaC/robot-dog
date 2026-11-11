#include <esp_arduino_version.h>

void initWiFi() {
  Serial.print("WiFi");
  WiFi.onEvent(WiFiEvent);
  if (WiFiMode == AP_MODE) {
    Serial.println("_AP");
    WiFiSetMode(AP_MODE);
  } else {
    Serial.println("_CLIENT");
    WiFiSetMode(WiFiMode);
  }
}

void WiFiEvent(WiFiEvent_t event){
    switch(event) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        case ARDUINO_EVENT_WIFI_AP_START:
#else
        case SYSTEM_EVENT_AP_START:
#endif
            WiFi.softAPsetHostname(wifiSsid[WiFiMode]);
            WiFiIP = WiFi.softAPIP();
            Serial.print("WiFi AP IP: ");
            Serial.println(WiFiIP);
            break;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        case ARDUINO_EVENT_WIFI_AP_STOP:
#else
        case SYSTEM_EVENT_AP_STOP:
#endif
            break;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        case ARDUINO_EVENT_WIFI_STA_START:
#else
        case SYSTEM_EVENT_STA_START:
#endif
            WiFi.setHostname(wifiSsid[WiFiMode]);
            break;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        case ARDUINO_EVENT_WIFI_STA_GOT_IP:
#else
        case SYSTEM_EVENT_STA_GOT_IP:
#endif
            WiFiIP = WiFi.localIP();
            Serial.print("WiFi STA IP: ");
            Serial.println(WiFiIP);
            break;
        default:
            break;
    }
}

void WiFiSetMode(int setWiFiMode) {
  WiFiMode = settingsSaveWiFi(setWiFiMode);
  if (WiFiMode == AP_MODE) {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(wifiSsid[WiFiMode], wifiPass[WiFiMode]);
  } else {
    WiFi.begin(wifiSsid[WiFiMode], wifiPass[WiFiMode]);
  }
}

void menuWiFiSetMode(int setWiFiMode) {
  WiFiSetMode(setWiFiMode);
}

void updateWiFi() {
  
}

double WiFiInfo(double info) {
  cliSerial->print("IP: ");
  cliSerial->println(WiFiIP);
  cliSerial->print("SSID: ");
  cliSerial->println(wifiSsid[WiFiMode]);

  return 1;
}
