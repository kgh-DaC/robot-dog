import { createHash, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';

const MAX_BODY_BYTES = 4096;
const MAX_COMMAND_TTL_MS = 3000;
const MAX_CLOCK_SKEW_MS = 5000;
const MAX_VECTOR_VALUE = 20000;

function tokenDigest(token) {
  return createHash('sha256').update(token, 'utf8').digest();
}

function createAuthenticator(tokens) {
  if (!Array.isArray(tokens) || tokens.length === 0) {
    throw new TypeError('At least one gateway token is required');
  }
  const entries = tokens.map(({ token, subject, role }) => {
    if (typeof token !== 'string' || token.length < 16 ||
        typeof subject !== 'string' || !subject ||
        !['viewer', 'operator'].includes(role)) {
      throw new TypeError('Gateway tokens require a token, subject, and valid role');
    }
    return { digest: tokenDigest(token), subject, role };
  });

  return function authenticate(request) {
    const header = request.headers.authorization;
    if (typeof header !== 'string' || !header.startsWith('Bearer ')) return null;
    const presented = tokenDigest(header.slice(7));
    for (const entry of entries) {
      if (timingSafeEqual(presented, entry.digest)) {
        return { subject: entry.subject, role: entry.role };
      }
    }
    return null;
  };
}

function sendJson(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
  });
  response.end(body);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let tooLarge = false;
    const chunks = [];
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        tooLarge = true;
        chunks.length = 0;
        return;
      }
      if (!tooLarge) chunks.push(chunk);
    });
    request.on('end', () => {
      if (tooLarge) {
        reject(Object.assign(new Error('Request body too large'), { status: 413 }));
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        reject(Object.assign(new Error('Invalid JSON'), { status: 400 }));
      }
    });
    request.on('error', reject);
  });
}

function validEnvelope(body) {
  return body !== null && typeof body === 'object' && !Array.isArray(body) &&
    Object.keys(body).every((key) =>
      ['requestId', 'sequence', 'issuedAt', 'expiresAt', 'command'].includes(key)) &&
    ['requestId', 'sequence', 'issuedAt', 'expiresAt', 'command'].every((key) =>
      Object.hasOwn(body, key)) &&
    typeof body.requestId === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.requestId) &&
    Number.isSafeInteger(body.sequence) && body.sequence >= 0 &&
    Number.isSafeInteger(body.issuedAt) && Number.isSafeInteger(body.expiresAt) &&
    body.command !== null && typeof body.command === 'object' && !Array.isArray(body.command);
}

function validateCommand(command) {
  if (command.type === 'ARM' || command.type === 'STOP') {
    return Object.keys(command).length === 1;
  }
  return command.type === 'MOVE' &&
    Object.keys(command).length === 2 &&
    Object.hasOwn(command, 'vector') &&
    Array.isArray(command.vector) && command.vector.length === 6 &&
    command.vector.every((value) =>
      Number.isInteger(value) && value >= 0 && value <= MAX_VECTOR_VALUE);
}

export function createGatewayServer({ tokens, device, clock = Date.now }) {
  if (!device || typeof device.telemetry !== 'function' ||
      typeof device.arm !== 'function' || typeof device.move !== 'function' ||
      typeof device.stop !== 'function') {
    throw new TypeError('A device adapter implementing telemetry, arm, move, and stop is required');
  }
  const authenticate = createAuthenticator(tokens);
  const lastSequence = new Map();
  const seenRequestIds = new Map();

  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (request.method === 'GET' && url.pathname === '/health') {
        sendJson(response, 200, { status: 'ok', mode: 'mock-only' });
        return;
      }

      const principal = authenticate(request);
      if (!principal) {
        sendJson(response, 401, { error: 'unauthorized' });
        return;
      }
      if (request.method === 'GET' && url.pathname === '/v1/telemetry') {
        sendJson(response, 200, device.telemetry());
        return;
      }
      if (request.method !== 'POST' || url.pathname !== '/v1/commands') {
        sendJson(response, 404, { error: 'not_found' });
        return;
      }
      if (principal.role !== 'operator') {
        sendJson(response, 403, { error: 'forbidden' });
        return;
      }

      const contentType = (request.headers['content-type'] || '').split(';', 1)[0].trim().toLowerCase();
      if (contentType !== 'application/json') {
        sendJson(response, 415, { error: 'unsupported_media_type' });
        return;
      }

      const body = await readJson(request);
      if (!validEnvelope(body) || !validateCommand(body.command)) {
        sendJson(response, 400, { error: 'invalid_command' });
        return;
      }

      const now = clock();
      const { type } = body.command;
      if (type !== 'STOP' &&
          (body.issuedAt > now + MAX_CLOCK_SKEW_MS ||
           body.expiresAt <= now ||
           body.expiresAt <= body.issuedAt ||
           body.expiresAt - body.issuedAt > MAX_COMMAND_TTL_MS)) {
        sendJson(response, 408, { error: 'command_expired_or_invalid_time' });
        return;
      }

      if (type === 'STOP') {
        device.stop();
        sendJson(response, 200, {
          requestId: body.requestId,
          state: 'stopped',
        });
        return;
      }

      if (body.sequence <= (lastSequence.get(principal.subject) ?? -1)) {
        sendJson(response, 409, { error: 'replayed_or_out_of_order_command' });
        return;
      }
      const requestKey = principal.subject + ':' + body.requestId;
      if (seenRequestIds.has(requestKey)) {
        sendJson(response, 409, { error: 'duplicate_request_id' });
        return;
      }
      lastSequence.set(principal.subject, body.sequence);
      seenRequestIds.set(requestKey, now);
      while (seenRequestIds.size > 2048) {
        seenRequestIds.delete(seenRequestIds.keys().next().value);
      }

      if (type === 'ARM') {
        if (!device.arm()) {
          sendJson(response, 409, { error: 'device_safety_interlock_rejected' });
          return;
        }
        sendJson(response, 200, { requestId: body.requestId, state: 'armed' });
        return;
      }
      if (!device.move(body.command.vector)) {
        sendJson(response, 409, { error: 'device_not_armed_or_disconnected' });
        return;
      }
      sendJson(response, 200, { requestId: body.requestId, state: 'accepted' });
    } catch (error) {
      if (error.status) {
        sendJson(response, error.status, { error: error.status === 413 ? 'request_too_large' : 'invalid_json' });
        return;
      }
      console.error('Mock gateway request failed:', error);
      if (!response.headersSent) sendJson(response, 500, { error: 'internal_error' });
      else response.destroy();
    }
  });

  return server;
}
