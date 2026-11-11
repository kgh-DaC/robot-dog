import { createGatewayServer } from './gateway.mjs';
import { MockDevice } from './mock-device.mjs';

const operatorToken = process.env.ROBOT_GATEWAY_OPERATOR_TOKEN;
const viewerToken = process.env.ROBOT_GATEWAY_VIEWER_TOKEN;
if (!operatorToken || !viewerToken) {
  console.error('Set ROBOT_GATEWAY_OPERATOR_TOKEN and ROBOT_GATEWAY_VIEWER_TOKEN to local test tokens.');
  process.exitCode = 1;
} else {
  const server = createGatewayServer({
    tokens: [
      { token: operatorToken, subject: 'local-operator', role: 'operator' },
      { token: viewerToken, subject: 'local-viewer', role: 'viewer' },
    ],
    device: new MockDevice(),
  });
  server.listen(Number(process.env.PORT) || 8787, '127.0.0.1', () => {
    console.log('Mock-only robot gateway listening on 127.0.0.1');
  });
}
