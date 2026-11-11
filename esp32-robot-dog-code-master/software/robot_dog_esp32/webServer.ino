void initWebServer() {
  Serial.print("WebServer ");
  initWebServerRoutes();
  Serial.println();
}

void initWebServerRoutes() {  
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request){
    AsyncWebServerResponse *response = request->beginResponse_P(200, "text/html",  index_html_gz, index_html_gz_len);
    response->addHeader("Content-Encoding", "gzip");
    request->send(response);  
  });
    
  // Dinamic config
  server.on("/c.js", HTTP_GET, [](AsyncWebServerRequest *request){
    request->send(200, "application/x-javascript", "var c={w:'ws://" + WiFiIP.toString() + "/ws'}");
  });

  ws.onEvent(onWsEvent);
  server.addHandler(&ws);
 
  server.begin();
}

static uint32_t activeControlClientId = UINT32_MAX;

bool canArmMovement() {
  bool sampleValid;
  unsigned long sampleAt;
  portENTER_CRITICAL(&telemetryMux);
  sampleValid = imuSampleValid;
  sampleAt = imuSampleAtMicros;
  portEXIT_CRITICAL(&telemetryMux);

  return imuReady && sampleValid &&
         (unsigned long)(micros() - sampleAt) <= IMU_TELEMETRY_STALE_US;
}

void onWsEvent(AsyncWebSocket * server, AsyncWebSocketClient * client, AwsEventType type, void * arg, uint8_t *data, size_t len){
 
  if(type == WS_EVT_CONNECT){ 
    if (activeControlClientId != UINT32_MAX) {
      client->close(1008, "controller already connected");
      return;
    }
    activeControlClientId = client->id();
    setFailsafe();
    client->text("Ok");
    clientOnline = true;
  } else if (clientOnline && client->id() == activeControlClientId && type == WS_EVT_DATA) {
    AwsFrameInfo *frameInfo = (AwsFrameInfo*)arg;
    if (frameInfo == nullptr || !frameInfo->final || frameInfo->index != 0 ||
        frameInfo->len != len || frameInfo->opcode != WS_BINARY ||
        data == nullptr || len < 1) {
      setFailsafe();
      Serial.println("Rejected incomplete WebSocket frame");
      return;
    }

    if (data[0] == P_STOP) {
      if (len != 1) {
        setFailsafe();
        client->text("Rejected malformed STOP packet");
        return;
      }
      setFailsafe();
      client->text("DISARMED");
      return;
    }

    if ((data[0] == P_MOVE && (len != 14 || data[1] > 1)) ||
        (data[0] == P_ARM && len != 2) ||
        (data[0] == P_TELEMETRY && (len != 2 || data[1] != 1))) {
      setFailsafe();
      Serial.println("Rejected malformed WebSocket packet");
      return;
    }

    switch(data[0]) {
      case P_MOVE:
        pMove(data);
        break;
      case P_ARM:
        setFailsafe();
        if (canArmMovement()) {
          portENTER_CRITICAL(&movementMux);
          pendingMovement = {{0, 0, 0}, {0, 0, 0}};
          pendingMovementUpdate = true;
          lastValidMoveAtMicros = micros();
          movementArmed = true;
          portEXIT_CRITICAL(&movementMux);
          client->text(String("ARMED:") + String(data[1]));
        } else {
          client->text(String("ARM_REJECTED:") + String(data[1]));
        }
        break;
      case P_TELEMETRY:
      {
        uint8_t telemetryPackage[P_TELEMETRY_LEN];
        pTelemetry(telemetryPackage);
        client->binary(telemetryPackage, P_TELEMETRY_LEN);
        break;
      }
      default:
        setFailsafe();
        Serial.print("UNKNOWN PACKAGE ID: ");
        Serial.println(data[0], DEC);
    }

  } else if(type == WS_EVT_DISCONNECT && client->id() == activeControlClientId){
    setFailsafe();
    clientOnline = false;
    activeControlClientId = UINT32_MAX;
  }
}
