export class MockDevice {
  constructor(clock = Date.now) {
    this.clock = clock;
    this.connected = true;
    this.imuReady = true;
    this.imuFresh = true;
    this.armed = false;
    this.lastCommand = null;
    this.vector = [10000, 10000, 10000, 10000, 10000, 10000];
  }

  telemetry() {
    return {
      schemaVersion: 1,
      deviceId: 'mock-robot-dog',
      sampledAt: new Date(this.clock()).toISOString(),
      connectionState: this.connected ? 'connected' : 'disconnected',
      armed: this.armed,
      imu: {
        ready: this.imuReady,
        fresh: this.imuFresh,
        axesDeg: [null, null, null],
      },
      joints: Array(12).fill(null).map((_, index) => ({
        id: 'joint-' + index,
        angleDeg: 90,
        source: 'commanded',
      })),
      power: {
        valid: false,
        voltageV: null,
        currentA: null,
        powerW: null,
      },
    };
  }

  arm() {
    if (!this.connected || !this.imuReady || !this.imuFresh) return false;
    this.armed = true;
    this.vector = [10000, 10000, 10000, 10000, 10000, 10000];
    this.lastCommand = 'ARM';
    return true;
  }

  move(vector) {
    if (!this.connected || !this.armed) return false;
    this.vector = vector.slice();
    this.lastCommand = 'MOVE';
    return true;
  }

  stop() {
    this.armed = false;
    this.vector = [10000, 10000, 10000, 10000, 10000, 10000];
    this.lastCommand = 'STOP';
  }
}
