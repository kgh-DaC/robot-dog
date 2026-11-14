// Optional "walk forward after power-on" (user request 2026-11-14).
//
// OFF by default. Switched with the serial command AUTOWALK ON / AUTOWALK OFF
// and stored in flash (Preferences "autoWalk"). When ON, a POWER-ON boot (not
// a brownout, watchdog, panic or software reset, so a reset loop cannot keep
// restarting the walk) waits AUTOWALK_DELAY_MS, then walks forward for
// AUTOWALK_RUN_MS and stops by itself (neutral pose, disarmed).
//
// There is no remote operator during this run, so the 1 s heartbeat lease
// cannot protect it: the firmware renews its own lease and the fixed run time
// is the bound. Everything else stays as it was: STOP from serial or Wi-Fi
// always works and ends the run, the tilt limit still stops it when the IMU
// works, and nobody else can ARM while it runs. Any STOP, ARM or serial pose
// command during the waiting time cancels the pending run.

const uint32_t AUTOWALK_DELAY_MS = 5000;
const uint32_t AUTOWALK_RUN_MS = 10000;

const uint8_t AUTOWALK_IDLE = 0, AUTOWALK_WAITING = 1, AUTOWALK_RUNNING = 2;
bool autoWalkEnabled = false;
uint8_t autoWalkPhase = AUTOWALK_IDLE;
unsigned long autoWalkAt = 0;  // WAITING: start time, RUNNING: end time

// Called once from setup(), before the web task starts.
void autoWalkSetup()
{
  Preferences store;
  if (store.begin("autoWalk", true)) {
    autoWalkEnabled = store.getBool("on", false);
    store.end();
  }
  if (autoWalkEnabled && esp_reset_reason() == ESP_RST_POWERON) {
    autoWalkPhase = AUTOWALK_WAITING;
    autoWalkAt = millis() + AUTOWALK_DELAY_MS;
    Serial.println("EVENT AUTOWALK_PENDING");
  }
}

// Called by controlStop(), by a successful ARM from serial/Wi-Fi and by the
// serial pose commands: a person has taken over, so the pending run is dropped.
void autoWalkCancel()
{
  autoWalkPhase = AUTOWALK_IDLE;
}

// Called from loop() with the control mutex held, before updateLease().
void autoWalkUpdate()
{
  if (autoWalkPhase == AUTOWALK_WAITING) {
    if ((long)(millis() - autoWalkAt) < 0 || startupHoming) return;
    const char *error = controlArm(CTRL_AUTO);
    if (error == NULL) error = controlWalk(CTRL_AUTO, WALK_FORWARD);
    if (error != NULL) {
      if (armed && controller == CTRL_AUTO) controlStop("autowalk_failed");
      autoWalkPhase = AUTOWALK_IDLE;
      Serial.print("EVENT AUTOWALK_SKIPPED ");
      Serial.println(error);
      return;
    }
    autoWalkPhase = AUTOWALK_RUNNING;
    autoWalkAt = millis() + AUTOWALK_RUN_MS;
    Serial.println("EVENT AUTOWALK_START");
    return;
  }
  if (autoWalkPhase != AUTOWALK_RUNNING) return;
  if (!armed || controller != CTRL_AUTO) {  // stopped elsewhere
    autoWalkPhase = AUTOWALK_IDLE;
    return;
  }
  if ((long)(millis() - autoWalkAt) >= 0) {
    controlStop("autowalk_done");  // also sets the phase to IDLE
    Serial.println("EVENT STOPPED autowalk_done");
    return;
  }
  leaseDeadline = millis() + LEASE_TIMEOUT_MS;
}

// Returns NULL on success or an error code. Switching ON never starts a walk
// now; it only takes effect at the next power-on.
const char *autoWalkSet(bool on)
{
  if (on && armed) return "armed";
  Preferences store;
  if (!store.begin("autoWalk", false)) return "autowalk_store_failed";
  const bool saved = store.putBool("on", on) == 1;
  store.end();
  if (!saved) return "autowalk_store_failed";
  autoWalkEnabled = on;
  if (!on && autoWalkPhase == AUTOWALK_WAITING) autoWalkPhase = AUTOWALK_IDLE;
  return NULL;
}

void printAutoWalk()
{
  Serial.printf("AUTOWALK enabled=%d phase=%s delay=%lu run=%lu\n", autoWalkEnabled ? 1 : 0,
                autoWalkPhase == AUTOWALK_WAITING ? "WAITING" : autoWalkPhase == AUTOWALK_RUNNING ? "RUNNING" : "IDLE",
                (unsigned long)AUTOWALK_DELAY_MS, (unsigned long)AUTOWALK_RUN_MS);
}
