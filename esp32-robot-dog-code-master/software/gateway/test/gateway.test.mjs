import assert from 'node:assert/strict';
import { once } from 'node:events';
import { afterEach, test } from 'node:test';
import { createGatewayServer } from '../src/gateway.mjs';
import { MockDevice } from '../src/mock-device.mjs';

const operatorToken = 'test-operator-token-0123456789';
const viewerToken = 'test-viewer-token-0123456789';
let server;
let baseUrl;
let now = 1_800_000_000_000;
let device;

async function startGateway() {
  device = new MockDevice(() => now);
  server = createGatewayServer({
    tokens: [
      { token: operatorToken, subject: 'operator-1', role: 'operator' },
      { token: viewerToken, subject: 'viewer-1', role: 'viewer' },
    ],
    device,
    clock: () => now,
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  baseUrl = 'http://127.0.0.1:' + server.address().port;
}

afterEach(async () => {
  if (server?.listening) {
    server.close();
    await once(server, 'close');
  }
  server = undefined;
});

function envelope(sequence, command, overrides = {}) {
  return {
    requestId: '1c92f198-7cae-4be8-8a7c-4c0967fa1430',
    sequence,
    issuedAt: now,
    expiresAt: now + 2000,
    command,
    ...overrides,
  };
}

async function sendCommand(token, body) {
  return fetch(baseUrl + '/v1/commands', {
    method: 'POST',
    headers: {
      authorization: 'Bearer ' + token,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  });
}

test('exposes mock health and requires authentication for telemetry', async () => {
  await startGateway();
  const health = await fetch(baseUrl + '/health');
  assert.equal(health.status, 200);
  assert.equal((await health.json()).mode, 'mock-only');

  const unauthenticated = await fetch(baseUrl + '/v1/telemetry');
  assert.equal(unauthenticated.status, 401);
  assert.deepEqual(await unauthenticated.json(), { error: 'unauthorized' });

  const telemetry = await fetch(baseUrl + '/v1/telemetry', {
    headers: { authorization: 'Bearer ' + viewerToken },
  });
  assert.equal(telemetry.status, 200);
  assert.equal((await telemetry.json()).deviceId, 'mock-robot-dog');
});

test('allows only operators to issue ARM and MOVE; enforces interlocks and bounds', async () => {
  await startGateway();
  const viewerResponse = await sendCommand(viewerToken, envelope(1, { type: 'ARM' }));
  assert.equal(viewerResponse.status, 403);
  assert.equal(device.armed, false);

  device.imuFresh = false;
  const interlock = await sendCommand(operatorToken, envelope(1, { type: 'ARM' }, {
    requestId: '23a77a80-ef62-4f69-8e61-b6d70be3a24e',
  }));
  assert.equal(interlock.status, 409);
  assert.equal(device.armed, false);

  device.imuFresh = true;
  const arm = await sendCommand(operatorToken, envelope(2, { type: 'ARM' }, {
    requestId: '3c2253a2-3865-4705-a052-30462d052b31',
  }));
  assert.equal(arm.status, 200);
  assert.equal(device.armed, true);

  const move = await sendCommand(operatorToken, envelope(3, {
    type: 'MOVE',
    vector: [0, 10000, 20000, 0, 10000, 20000],
  }, { requestId: '25db26f0-56ec-4c23-9b7a-65c2ecbf5b40' }));
  assert.equal(move.status, 200);
  assert.deepEqual(device.vector, [0, 10000, 20000, 0, 10000, 20000]);

  const invalid = await sendCommand(operatorToken, envelope(4, {
    type: 'MOVE',
    vector: [0, 10000, 20001, 0, 10000, 20000],
  }, { requestId: 'e0be941b-6e8b-4513-b9a2-70954843174e' }));
  assert.equal(invalid.status, 400);
});

test('rejects expired, overlong, future and replayed movement envelopes', async () => {
  await startGateway();
  const expired = await sendCommand(operatorToken, envelope(1, { type: 'ARM' }, {
    expiresAt: now,
  }));
  assert.equal(expired.status, 408);

  const overlong = await sendCommand(operatorToken, envelope(1, { type: 'ARM' }, {
    expiresAt: now + 3001,
  }));
  assert.equal(overlong.status, 408);

  const future = await sendCommand(operatorToken, envelope(1, { type: 'ARM' }, {
    issuedAt: now + 5001,
    expiresAt: now + 6000,
  }));
  assert.equal(future.status, 408);

  const arm = await sendCommand(operatorToken, envelope(2, { type: 'ARM' }));
  assert.equal(arm.status, 200);
  const replay = await sendCommand(operatorToken, envelope(2, { type: 'ARM' }, {
    requestId: '9e3e18b4-0406-4d4e-804a-8fd501fd5da8',
  }));
  assert.equal(replay.status, 409);
});

test('STOP bypasses expiry and sequence order without consuming movement sequence', async () => {
  await startGateway();
  device.armed = true;
  const staleStop = await sendCommand(operatorToken, envelope(999, { type: 'STOP' }, {
    issuedAt: now - 60_000,
    expiresAt: now - 30_000,
  }));
  assert.equal(staleStop.status, 200);
  assert.equal(device.armed, false);
  assert.equal(device.lastCommand, 'STOP');

  const arm = await sendCommand(operatorToken, envelope(1, { type: 'ARM' }, {
    requestId: '51e0450f-525e-4e42-98e5-f2e4ade2ab12',
  }));
  assert.equal(arm.status, 200);

  const unauthorized = await sendCommand(viewerToken, envelope(1, { type: 'STOP' }));
  assert.equal(unauthorized.status, 403);
});

test('rejects malformed command envelopes and oversized request bodies', async () => {
  await startGateway();
  const malformed = await sendCommand(operatorToken, { sequence: 1 });
  assert.equal(malformed.status, 400);

  const extraEnvelopeField = await sendCommand(operatorToken, envelope(1, { type: 'ARM' }, {
    admin: true,
    requestId: '4ddfcaaa-93a4-4a9b-8b5a-58f436b7f5a2',
  }));
  assert.equal(extraEnvelopeField.status, 400);

  const extraCommandField = await sendCommand(operatorToken, envelope(1, {
    type: 'ARM',
    overrideInterlocks: true,
  }, { requestId: '44d06ab3-a83a-45b0-9303-dbb20c4a4b11' }));
  assert.equal(extraCommandField.status, 400);

  const unsupportedMediaType = await fetch(baseUrl + '/v1/commands', {
    method: 'POST',
    headers: {
      authorization: 'Bearer ' + operatorToken,
      'content-type': 'text/plain',
    },
    body: JSON.stringify(envelope(1, { type: 'ARM' })),
  });
  assert.equal(unsupportedMediaType.status, 415);

  const tooLarge = await fetch(baseUrl + '/v1/commands', {
    method: 'POST',
    headers: {
      authorization: 'Bearer ' + operatorToken,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ padding: 'x'.repeat(5000) }),
  });
  assert.equal(tooLarge.status, 413);
});
