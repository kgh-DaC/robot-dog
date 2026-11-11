const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class MockRobotDevice {
  constructor() {
    this.now = 0;
    this.lastImuAt = 0;
    this.lastMoveAt = 0;
    this.imuReady = true;
    this.imuValid = true;
    this.armed = false;
  }

  receive(packet) {
    if (packet[0] === 83 && packet.length === 1) {
      this.armed = false;
      return 'DISARMED';
    }
    if (packet[0] === 65 && packet.length === 2) {
      this.armed = false;
      if (!this.imuReady || !this.imuValid || this.now - this.lastImuAt > 20000) {
        return 'ARM_REJECTED:' + packet[1];
      }
      this.armed = true;
      this.lastMoveAt = this.now;
      return 'ARMED:' + packet[1];
    }
    if (packet[0] === 77 && packet.length === 14) {
      if (packet[1] === 0) {
        this.armed = false;
        return;
      }
      if (!this.armed) return;
      for (let offset = 2; offset < 14; offset += 2) {
        if (((packet[offset] << 8) | packet[offset + 1]) > 20000) {
          this.armed = false;
          return;
        }
      }
      this.lastMoveAt = this.now;
      return;
    }
    if (packet[0] === 84 && packet.length === 2 && packet[1] === 1) {
      return this.telemetry();
    }
    this.armed = false;
  }

  telemetry() {
    const bytes = new Uint8Array(40);
    bytes[0] = 84;
    bytes[1] = 2;
    let status = (this.imuReady ? 1 : 0) |
      (this.imuValid ? 2 : 0) | (this.armed ? 16 : 0);
    bytes[2] = status >> 8;
    bytes[3] = status & 255;
    return bytes;
  }

  advanceTime(milliseconds, refreshImu = true) {
    this.now += milliseconds * 1000;
    if (refreshImu && this.imuReady && this.imuValid) this.lastImuAt = this.now;
    if (this.armed &&
        (!this.imuReady || !this.imuValid || this.now - this.lastImuAt > 20000 ||
         this.now - this.lastMoveAt >= 80000)) {
      this.armed = false;
    }
  }

  reconnect() {
    this.armed = false;
  }
}

function createDashboard({ search = '' } = {}) {
  let wallClock = 1000;
  let monotonicClock = 0;
  const elements = new Map();
  const errors = [];
  const timeouts = new Map();
  const intervals = new Map();
  let nextTimerId = 1;
  let socketCount = 0;
  let confirmResult = true;
  const circles = Array.from({ length: 12 }, (_, index) => ({
    dataset: { joint: String(index) },
    style: {},
    getAttribute(name) {
      return name === 'data-joint' ? this.dataset.joint : null;
    },
  }));
  const storage = new Map();
  const createElement = () => {
    const element = {
      children: [],
      listeners: {},
      style: {},
      classList: { add() {}, remove() {} },
      hidden: true,
      offsetLeft: 0,
      offsetTop: 0,
      offsetWidth: 100,
      offsetHeight: 100,
      appendChild(child) {
        this.children.push(child);
        register(child);
      },
      addEventListener(name, callback) {
        this.listeners[name] = callback;
      },
      dispatch(name) {
        if (this.listeners[name]) this.listeners[name]();
      },
      click() {},
      setAttribute(name, value) {
        this[name] = value;
      },
    };
    Object.defineProperty(element, 'id', {
      set(value) {
        this._id = value;
        elements.set(value, this);
      },
      get() {
        return this._id;
      },
    });
    return element;
  };
  const register = (element) => {
    if (element.id) elements.set(element.id, element);
    element.children.forEach(register);
  };
  [
    'status', 'demo-notice', 'body_rotate', 'joint-cards', 'log-status', 'control-status',
    'arm-control', 'stop-control',
    'export-log', 'clear-log', 'voltage', 'current', 'power', 'energy',
    'imu-x', 'imu-y', 'imu-z',
  ].forEach((id) => {
    const element = createElement();
    element.id = id;
    elements.set(id, element);
  });
  ['leftJ', 'rightJ'].forEach((id) => {
    const element = createElement();
    element.id = id;
    elements.set(id, element);
  });
  const FakeDate = class extends Date {
    static now() {
      return wallClock;
    }
  };
  const context = {
    document: {
      visibilityState: 'visible',
      addEventListener() {},
      createElement,
      getElementById(id) {
        if (!elements.has(id)) {
          const element = createElement();
          element.id = id;
        }
        return elements.get(id);
      },
      querySelectorAll(selector) {
        return selector === '[data-joint]' ? circles : [];
      },
    },
    window: {
      location: { search },
      confirm() {
        return confirmResult;
      },
    },
    localStorage: {
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      setItem(key, value) {
        storage.set(key, value);
      },
      removeItem(key) {
        storage.delete(key);
      },
    },
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    Blob,
    WebSocket: class {
      constructor() {
        socketCount++;
        this.readyState = 1;
        this.sent = [];
      }
      send(data) {
        this.sent.push(data);
      }
    },
    c: { w: 'ws://localhost/ws' },
    setInterval(callback, delay) {
      const id = nextTimerId++;
      intervals.set(id, { callback, delay });
      return id;
    },
    clearInterval(id) {
      intervals.delete(id);
    },
    setTimeout(callback, delay) {
      const id = nextTimerId++;
      timeouts.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) {
      timeouts.delete(id);
    },
    console: {
      error(...args) {
        errors.push(args);
      },
    },
    Date: FakeDate,
    performance: {
      now() {
        return monotonicClock;
      },
    },
    Uint8Array,
    ArrayBuffer,
    DataView,
    URLSearchParams,
    Math,
    parseInt,
  };
  const sourcePath = path.join(__dirname, '..', 'src', 's.js');
  const source = fs.readFileSync(sourcePath, 'utf8') +
    '\nglobalThis.testTelemetry = telemetry; globalThis.testTelemetryLog = telemetryLog; globalThis.testGui = gui; globalThis.testWs = ws; globalThis.testPacket = packet; globalThis.testDemoMode = demoMode; globalThis.testDemo = demo;';
  vm.runInNewContext(source, context);
  context.testGui.init();
  context.testPacket.init();

  return {
    context,
    elements,
    circles,
    storage,
    errors,
    timeouts,
    intervals,
    getSocketCount() {
      return socketCount;
    },
    fireInterval(id) {
      const interval = intervals.get(id);
      assert.ok(interval, 'expected a scheduled interval');
      interval.callback();
    },
    fireTimeout(id) {
      const timeout = timeouts.get(id);
      assert.ok(timeout, 'expected a scheduled timeout');
      timeouts.delete(id);
      timeout.callback();
      return timeout.delay;
    },
    advanceTime(milliseconds) {
      wallClock += milliseconds;
      monotonicClock += milliseconds;
    },
    getMonotonicTime() {
      return monotonicClock;
    },
    setConfirmResult(value) {
      confirmResult = value;
    },
    jumpWallClock(milliseconds) {
      wallClock += milliseconds;
    },
    makeV2({ status = 15, imu = [-125, 250, -300], joints, voltage = 12000, current = 1000 } = {}) {
      const bytes = new Uint8Array(40);
      bytes[0] = 84;
      bytes[1] = 2;
      const write = (offset, value) => {
        const encoded = value & 0xffff;
        bytes[offset] = encoded >> 8;
        bytes[offset + 1] = encoded & 0xff;
      };
      write(2, status);
      imu.forEach((value, index) => write(4 + index * 2, value));
      (joints || Array(12).fill(9000)).forEach((value, index) => write(10 + index * 2, value));
      write(34, voltage);
      write(36, current);
      write(38, 400);
      return bytes;
    },
  };
}

test('demo mode renders synthetic telemetry without opening a socket or persisting logs', () => {
  const dashboard = createDashboard({ search: '?demo=1' });
  const { context, elements, storage } = dashboard;

  assert.equal(context.testDemoMode, true);
  assert.equal(dashboard.getSocketCount(), 0);
  assert.equal(elements.get('demo-notice').hidden, false);
  assert.match(elements.get('status').textContent, /모의 미리보기.*ESP32 연결 안 됨/);
  assert.match(elements.get('control-status').textContent, /읽기 전용/);
  assert.equal(elements.get('arm-control').disabled, true);
  assert.equal(elements.get('stop-control').disabled, true);
  assert.equal(elements.get('export-log').disabled, true);
  assert.equal(elements.get('clear-log').disabled, true);
  assert.equal(elements.get('leftJ')['aria-disabled'], 'true');

  context.testGui.update();
  assert.match(elements.get('voltage').textContent, /^\d+\.\d{3} V$/);
  assert.match(elements.get('imu-x').textContent, /^-?\d+\.\d{2}°$/);
  assert.notEqual(elements.get('joint-0').textContent, '—');
  assert.equal(storage.size, 0);

  dashboard.advanceTime(250);
  dashboard.fireInterval(context.testDemo.interval);
  dashboard.advanceTime(250);
  dashboard.fireInterval(context.testDemo.interval);
  assert.ok(context.testTelemetryLog.records.length >= 2);
  assert.notEqual(elements.get('voltage-path').d, '');
  assert.equal(storage.size, 0);

  context.testWs.arm();
  context.testWs.stop();
  assert.equal(dashboard.getSocketCount(), 0);
  assert.equal(context.testWs.armed, false);
  assert.equal(storage.size, 0);
});

test('decodes v2 IMU, commanded joints and signed power; updates dashboard', () => {
  const dashboard = createDashboard();
  const { context, elements, circles } = dashboard;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());
  context.testGui.update();

  assert.equal(context.testTelemetry.imuAxes[0], -125);
  assert.equal(context.testTelemetry.joints[0], 90);
  assert.equal(context.testTelemetry.voltage, 12);
  assert.equal(elements.get('current').textContent, '1.000 A');
  assert.equal(elements.get('power').textContent, '12.000 W');
  assert.equal(elements.get('imu-x').textContent, '-1.25°');
  assert.equal(elements.get('joint-0').textContent, '90.00°');
  assert.notEqual(circles[0].style.fill, '#52646b');
});

test('integrates energy only across contiguous fresh power samples and persists logs', () => {
  const dashboard = createDashboard();
  const { context, storage } = dashboard;
  context.testPacket.telemetryParse(dashboard.makeV2({ voltage: 12000, current: 1000 }));
  dashboard.advanceTime(1000);
  context.testPacket.telemetryParse(dashboard.makeV2({ voltage: 12000, current: 2000 }));

  assert.ok(Math.abs(context.testTelemetry.voltage - 12) < 1e-9);
  assert.ok(Math.abs(JSON.parse(storage.get('robotDogTelemetryV1')).at(-1).energyWh - 0.005) < 1e-9);
  assert.notEqual(dashboard.elements.get('voltage-path').d, '');
  assert.notEqual(dashboard.elements.get('power-path').d, '');
  dashboard.advanceTime(3000);
  context.testPacket.telemetryParse(dashboard.makeV2({ voltage: 12000, current: 1000 }));
  assert.ok(Math.abs(context.testTelemetry.voltage - 12) < 1e-9);
  assert.equal(JSON.parse(storage.get('robotDogTelemetryV1')).length, 3);
});

test('uses monotonic time for energy integration and telemetry freshness', () => {
  const dashboard = createDashboard();
  const { context, storage, elements } = dashboard;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());
  dashboard.jumpWallClock(-60 * 60 * 1000);
  dashboard.advanceTime(1000);
  context.testPacket.telemetryParse(dashboard.makeV2());

  const records = JSON.parse(storage.get('robotDogTelemetryV1'));
  assert.ok(Math.abs(records.at(-1).energyWh - 12 / 3600) < 1e-9);
  context.testGui.update();
  assert.match(elements.get('status').textContent, /^연결됨/);

  dashboard.jumpWallClock(24 * 60 * 60 * 1000);
  dashboard.advanceTime(3001);
  context.testGui.update();
  assert.match(elements.get('status').textContent, /오래됨/);
});

test('records unavailable sensor readings as null instead of zero', () => {
  const dashboard = createDashboard();
  const { context, storage } = dashboard;
  context.testPacket.telemetryParse(dashboard.makeV2({ status: 1 }));
  const record = JSON.parse(storage.get('robotDogTelemetryV1'))[0];

  assert.equal(record.imuXDeg, null);
  assert.equal(record.voltageV, null);
  assert.equal(record.currentA, null);
  assert.equal(record.powerW, null);
});

test('supports v1 read-only telemetry and rejects truncated or unknown frames', () => {
  const dashboard = createDashboard();
  const { context } = dashboard;
  const v1 = new Uint8Array(14);
  v1[0] = 84;
  v1[1] = 1;
  v1[4] = 0x2e;
  v1[5] = 0xe0;
  context.testPacket.telemetryParse(v1);
  assert.equal(context.testTelemetry.version, 1);
  assert.equal(context.testTelemetry.voltage, 12);

  context.testPacket.telemetryParse(new Uint8Array([84, 2, 0]));
  assert.equal(context.testTelemetry.lastSampleAt, null);
  context.testPacket.telemetryParse(new Uint8Array([84, 9, ...Array(40).fill(0)]));
  assert.equal(context.testTelemetry.version, 9);
  assert.equal(context.testTelemetry.lastSampleAt, null);
});

test('requires a matching ARM acknowledgement and STOP blocks further movement packets', () => {
  const dashboard = createDashboard();
  const { context } = dashboard;
  const socket = context.testWs.ws;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());
  context.testWs.arm();

  assert.deepEqual(Array.from(new Uint8Array(socket.sent[0])), [65, 1]);
  context.testWs.update();
  assert.equal(socket.sent.length, 1);
  context.testWs.handleControlMessage('ARMED:0');
  assert.equal(context.testWs.armed, false);
  context.testWs.handleControlMessage('ARMED:1');
  assert.equal(context.testWs.armed, true);
  assert.deepEqual(Array.from(new Uint8Array(socket.sent[1]))[0], 77);

  context.testTelemetry.joints[0] = 90;
  context.testWs.stop();
  assert.equal(context.testWs.armed, false);
  assert.deepEqual(Array.from(new Uint8Array(socket.sent[2])), [83]);
  context.testWs.update();
  assert.equal(socket.sent.length, 3);
  assert.equal(context.testTelemetry.joints[0], 90);
});

test('sends the exact two-byte telemetry request without reusing the movement buffer', () => {
  const dashboard = createDashboard();
  const { context } = dashboard;
  const socket = context.testWs.ws;
  context.testWs.status = true;
  context.testWs.telemetryRequest();

  assert.deepEqual(Array.from(socket.sent[0]), [84, 1]);
  assert.equal(socket.sent[0].length, 2);
});

test('disarms and reports a transport error when a WebSocket send fails', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  const socket = context.testWs.ws;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());
  context.testWs.armed = true;
  socket.send = () => {
    throw new Error('socket closed during send');
  };
  context.testWs.update();

  assert.equal(context.testWs.armed, false);
  assert.equal(context.testWs.status, false);
  assert.match(elements.get('control-status').textContent, /송신 실패/);
  assert.match(dashboard.errors[0][0], /WebSocket connection failed/);
});

test('failed ARM transmission clears the pending request without arming', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());
  context.testWs.ws.send = () => {
    throw new Error('ARM send failed');
  };

  context.testWs.arm();

  assert.equal(context.testWs.armed, false);
  assert.equal(context.testWs.pendingArmSequence, null);
  assert.equal(context.testWs.status, false);
  assert.match(elements.get('control-status').textContent, /송신 실패/);
});

test('failed STOP transmission remains locally disarmed and is reported', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  context.testWs.status = true;
  context.testWs.armed = true;
  context.testWs.ws.send = () => {
    throw new Error('STOP send failed');
  };

  context.testWs.stop();

  assert.equal(context.testWs.armed, false);
  assert.equal(context.testWs.status, false);
  assert.match(elements.get('control-status').textContent, /송신 실패/);
});

test('WebSocket close and error events disarm and report disconnected state', () => {
  const closed = createDashboard();
  closed.context.testWs.init();
  closed.context.testWs.status = true;
  closed.context.testWs.armed = true;
  closed.context.testWs.ws.onclose();
  assert.equal(closed.context.testWs.armed, false);
  assert.equal(closed.context.testWs.status, false);
  assert.match(closed.elements.get('control-status').textContent, /연결 종료/);

  const errored = createDashboard();
  errored.context.testWs.init();
  errored.context.testWs.status = true;
  errored.context.testWs.armed = true;
  errored.context.testWs.ws.onerror(new Error('network failure'));
  assert.equal(errored.context.testWs.armed, false);
  assert.equal(errored.context.testWs.status, false);
  assert.match(errored.elements.get('control-status').textContent, /WebSocket 오류/);
  assert.match(errored.errors[0][0], /WebSocket connection failed/);
});

test('reconnects with backoff but never resumes movement automatically', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  const socketState = context.testWs;
  socketState.init();
  const firstSocket = socketState.ws;
  socketState.status = true;
  socketState.armed = true;
  firstSocket.readyState = 3;
  firstSocket.onclose();

  assert.equal(socketState.armed, false);
  assert.match(elements.get('control-status').textContent, /1초 후 재연결/);
  const timeoutId = socketState.reconnectTimeout;
  assert.equal(dashboard.fireTimeout(timeoutId), 1000);

  const reconnectedSocket = socketState.ws;
  assert.notEqual(reconnectedSocket, firstSocket);
  assert.equal(socketState.status, false);
  assert.equal(socketState.armed, false);
  reconnectedSocket.onopen();
  assert.equal(socketState.status, true);
  assert.equal(socketState.armed, false);
  assert.equal(reconnectedSocket.sent.length, 0);
});

test('caps the WebSocket reconnection delay at 30 seconds', () => {
  const dashboard = createDashboard();
  const socketState = dashboard.context.testWs;
  socketState.init();
  const expectedDelays = [1000, 2000, 4000, 8000, 16000, 30000, 30000];

  expectedDelays.forEach((delay) => {
    const socket = socketState.ws;
    socket.readyState = 3;
    socket.onclose();
    assert.equal(dashboard.fireTimeout(socketState.reconnectTimeout), delay);
  });
});

test('ignores delayed telemetry from the old socket after reconnecting', async () => {
  const dashboard = createDashboard();
  const { context } = dashboard;
  const socketState = context.testWs;
  socketState.init();
  const oldSocket = socketState.ws;
  oldSocket.onopen();
  context.testPacket.telemetryParse(dashboard.makeV2());
  assert.notEqual(context.testTelemetry.lastSampleAt, null);
  assert.notEqual(context.testTelemetryLog.lastSampleAt, null);

  let resolveFrame;
  const frame = new Promise((resolve) => {
    resolveFrame = resolve;
  });
  oldSocket.onmessage({ data: { arrayBuffer: () => frame } });
  oldSocket.readyState = 3;
  oldSocket.onclose();
  assert.equal(context.testTelemetry.lastSampleAt, null);
  assert.equal(context.testTelemetryLog.lastSampleAt, null);
  assert.equal(context.testTelemetryLog.lastPower, null);
  dashboard.fireTimeout(socketState.reconnectTimeout);
  socketState.ws.onopen();

  resolveFrame(dashboard.makeV2());
  await new Promise(setImmediate);
  assert.equal(context.testTelemetry.lastSampleAt, null);
});

test('disarms when a packet is queued after the socket has begun closing', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  context.testWs.status = true;
  context.testWs.armed = true;
  context.testWs.ws.readyState = 2;
  context.testWs.sendPacket(new Uint8Array([83]));

  assert.equal(context.testWs.status, false);
  assert.equal(context.testWs.armed, false);
  assert.match(elements.get('control-status').textContent, /연결이 닫혀 패킷을 보내지 못했습니다/);
});

test('requires explicit user confirmation before sending ARM', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  const socket = context.testWs.ws;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());

  dashboard.setConfirmResult(false);
  elements.get('arm-control').dispatch('click');
  assert.equal(socket.sent.length, 0);

  dashboard.setConfirmResult(true);
  elements.get('arm-control').dispatch('click');
  assert.deepEqual(Array.from(new Uint8Array(socket.sent[0])), [65, 1]);
});

test('disarms when the device rejects ARM or reports disarmed telemetry', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  context.testWs.status = true;
  context.testPacket.telemetryParse(dashboard.makeV2());
  context.testWs.arm();
  context.testWs.handleControlMessage('ARM_REJECTED:1');
  assert.equal(context.testWs.armed, false);
  assert.match(elements.get('control-status').textContent, /ARM 거부/);

  context.testWs.arm();
  context.testWs.handleControlMessage('ARMED:2');
  assert.equal(context.testWs.armed, true);
  context.testPacket.telemetryParse(dashboard.makeV2({ status: 15 }));
  assert.equal(context.testWs.armed, false);

  context.testWs.arm();
  context.testWs.handleControlMessage('ARMED:3');
  dashboard.advanceTime(3001);
  context.testGui.update();
  assert.equal(context.testWs.armed, false);
});

test('sends STOP when IMU validity is lost while the device reports armed', () => {
  const dashboard = createDashboard();
  const { context } = dashboard;
  const socket = context.testWs.ws;
  context.testWs.status = true;
  context.testWs.armed = true;
  context.testPacket.telemetryParse(dashboard.makeV2({ status: 29 }));
  context.testGui.update();

  assert.equal(context.testWs.armed, false);
  assert.deepEqual(Array.from(socket.sent.at(-1)), [83]);
});

test('disconnect and stale samples do not present live joint values', () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  context.testPacket.telemetryParse(dashboard.makeV2());
  context.testWs.status = true;
  context.testWs.armed = true;
  context.testWs.status = false;
  context.testWs.ws.onclose();
  context.testGui.update();
  assert.equal(context.testWs.armed, false);
  assert.equal(elements.get('joint-0').textContent, '—');
  assert.equal(elements.get('status').textContent, 'ESP32 연결 끊김');

  context.testWs.status = true;
  dashboard.advanceTime(4000);
  context.testGui.update();
  assert.equal(elements.get('joint-0').textContent, '—');
  assert.match(elements.get('status').textContent, /오래됨/);
});

test('mock device exercises ARM, STOP, IMU loss, heartbeat timeout and reconnect', async () => {
  const dashboard = createDashboard();
  const { context, elements } = dashboard;
  const device = new MockRobotDevice();
  const socket = context.testWs.ws;
  const sentPackets = [];
  socket.send = (data) => {
    const packetBytes = Array.from(new Uint8Array(data));
    sentPackets.push(packetBytes);
    const response = device.receive(packetBytes);
    if (typeof response === 'string' && response.startsWith('ARMED:')) {
      queueMicrotask(() => context.testWs.handleControlMessage(response));
    } else if (typeof response === 'string' && response.startsWith('ARM_REJECTED:')) {
      queueMicrotask(() => context.testWs.handleControlMessage(response));
    } else if (response instanceof Uint8Array) {
      context.testPacket.telemetryParse(response);
    }
  };
  context.testWs.status = true;
  context.testWs.telemetryRequest();
  assert.equal(context.testTelemetry.sensorStatus & 3, 3);

  elements.get('arm-control').dispatch('click');
  await new Promise(setImmediate);
  assert.equal(device.armed, true);
  assert.equal(context.testWs.armed, true);
  assert.ok(sentPackets.some((packetBytes) => packetBytes[0] === 77));

  context.testWs.stop();
  assert.equal(device.armed, false);
  assert.equal(context.testWs.armed, false);

  elements.get('arm-control').dispatch('click');
  await new Promise(setImmediate);
  assert.equal(device.armed, true);
  device.imuValid = false;
  device.advanceTime(21);
  assert.equal(device.armed, false);
  context.testWs.telemetryRequest();
  assert.equal(context.testTelemetry.sensorStatus & 16, 0);
  assert.equal(context.testWs.armed, false);

  device.imuValid = true;
  device.lastImuAt = device.now;
  dashboard.advanceTime(3001);
  device.advanceTime(3001, false);
  context.testGui.update();
  assert.equal(device.receive([65, 77]), 'ARM_REJECTED:77');
  assert.match(elements.get('control-status').textContent, /ARM 확인|비활성화/);

  device.lastImuAt = device.now;
  assert.equal(device.receive([65, 78]), 'ARMED:78');
  device.advanceTime(81);
  assert.equal(device.armed, false);

  device.lastImuAt = device.now;
  assert.equal(device.receive([65, 79]), 'ARMED:79');
  device.receive([77, 1, 0x4e, 0x21, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
  assert.equal(device.armed, false);

  device.lastImuAt = device.now;
  assert.equal(device.receive([65, 80]), 'ARMED:80');
  device.reconnect();
  assert.equal(device.armed, false);
});

test('mock device enforces movement bounds, legacy STOP and exact heartbeat timeout', () => {
  const device = new MockRobotDevice();
  const arm = (sequence) => {
    const response = device.receive([65, sequence]);
    assert.equal(response, 'ARMED:' + sequence);
  };
  const move = (value, stopFlag = 1) => {
    const bytes = new Uint8Array(14);
    bytes[0] = 77;
    bytes[1] = stopFlag;
    for (let offset = 2; offset < 14; offset += 2) {
      bytes[offset] = value >> 8;
      bytes[offset + 1] = value & 255;
    }
    return Array.from(bytes);
  };

  arm(1);
  device.advanceTime(79);
  assert.equal(device.armed, true);
  device.receive(move(20000));
  assert.equal(device.armed, true);
  device.advanceTime(79);
  assert.equal(device.armed, true);
  device.advanceTime(1);
  assert.equal(device.armed, false);

  device.lastImuAt = device.now;
  arm(2);
  device.receive(move(20001));
  assert.equal(device.armed, false);

  device.lastImuAt = device.now;
  arm(3);
  device.receive(move(10000, 0));
  assert.equal(device.armed, false);
});
