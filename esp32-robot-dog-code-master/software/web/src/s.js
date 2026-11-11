function G(id) {
	return document.getElementById(id);
}

function monotonicNow() {
	return performance.now();
}

var demoMode = new URLSearchParams(window.location.search).get('demo') === '1';

var vector = {
	move: {
		x: 0.0,
		y: 0.0,
		z: 0.0,
	},
	rotate: {
		pitch: 0.0,
		roll:  0.0,
		yaw:   0.0,
	},
};

var telemetry = {
	version: null,
	sensorStatus: 0,
	voltage: null,
	current: null,
	loopTime: null,
	imuAxes: [null, null, null],
	joints: Array(12).fill(null),
	lastSampleAt: null,
};

var jointNames = ['좌측 전방', '우측 전방', '좌측 후방', '우측 후방'];
var jointAxes = ['알파', '베타', '감마'];
var logStorageKey = 'robotDogTelemetryV1';
var telemetryLog = {
	records: [],
	energyWh: 0,
	lastPower: null,
	lastSampleAt: null,
	available: true,
};

var gui ={
	updateInterval: null,
	obj: {
		status: null,
		body_rotate: null,
		jointCards: null,
		logStatus: null,
		controlStatus: null,
	},

	init: function () {
		document.addEventListener("visibilitychange", gui.onVisibilityChange);
		gui.obj.status = G('status');
		if (demoMode) G('demo-notice').hidden = false;
		gui.obj.body_rotate = G('body_rotate');
		gui.obj.jointCards = G('joint-cards');
		gui.obj.logStatus = G('log-status');
		gui.obj.controlStatus = G('control-status');
		gui.initJointCards();
		if (demoMode) {
			telemetryLog.records = [];
			telemetryLog.energyWh = 0;
			telemetryLog.lastPower = null;
			telemetryLog.lastSampleAt = null;
			gui.updateLogStatus('모의 데이터 미리보기입니다. 로그 저장과 내보내기는 비활성화됩니다.');
		} else {
			gui.initLogging();
		}
		G('export-log').addEventListener('click', gui.exportLog);
		G('clear-log').addEventListener('click', gui.clearLog);
		G('export-log').disabled = demoMode;
		G('clear-log').disabled = demoMode;
		G('arm-control').addEventListener('click', gui.armControl);
		G('stop-control').addEventListener('click', ws.stop);
		gui.updateControlState();

		gui.updateInterval = setInterval(gui.update, 100);
	},

	update: function () {
		if (ws.armed && !gui.armEligible()) ws.stop();
		gui.updateStatus();
		gui.updateTelemetry();
		gui.updateControlState();
	},

	armEligible: function () {
		return !demoMode && ws.status && telemetry.version === 2 &&
			telemetry.lastSampleAt !== null &&
			monotonicNow() - telemetry.lastSampleAt <= 3000 &&
			(telemetry.sensorStatus & 3) === 3;
	},

	armControl: function () {
		if (!gui.armEligible() || ws.armed || ws.pendingArmSequence !== null) return;
		if (!window.confirm('로봇이 고정되어 있고 전원 차단 장치에 접근할 수 있습니까? ARM 후에는 STOP을 누르거나 연결이 끊길 때까지 조이스틱 명령을 보냅니다.')) return;
		ws.arm();
	},

	updateControlState: function () {
		let armButton = G('arm-control');
		if (!armButton || !gui.obj.controlStatus) return;
		if (demoMode) {
			armButton.disabled = true;
			G('stop-control').disabled = true;
			G('leftJ').setAttribute('aria-disabled', 'true');
			G('rightJ').setAttribute('aria-disabled', 'true');
			gui.obj.controlStatus.textContent = '모의 미리보기 | 읽기 전용 · 로봇 제어 연결 없음';
			return;
		}
		armButton.disabled = !gui.armEligible() || ws.armed || ws.pendingArmSequence !== null;
		let disabled = !ws.armed;
		G('leftJ').setAttribute('aria-disabled', String(disabled));
		G('rightJ').setAttribute('aria-disabled', String(disabled));
		if (ws.pendingArmSequence !== null) {
			gui.obj.controlStatus.textContent = 'ARM 요청 확인 대기 중';
		} else if (ws.armed) {
			gui.obj.controlStatus.textContent = '보행 활성화 | STOP 또는 연결 감시 정지로 비활성화';
		} else {
			gui.obj.controlStatus.textContent = ws.controlMessage || '비활성화 | 명시적 ARM 대기 중';
		}
	},

	initJointCards: function () {
		jointNames.forEach(function (name, legIndex) {
			let card = document.createElement('div');
			card.className = 'joint-card';
			let heading = document.createElement('h3');
			heading.textContent = name;
			card.appendChild(heading);
			jointAxes.forEach(function (axis, axisIndex) {
				let row = document.createElement('div');
				row.className = 'joint-value';
				let label = document.createElement('span');
				label.textContent = axis;
				let value = document.createElement('strong');
				value.id = 'joint-' + (legIndex * 3 + axisIndex);
				value.textContent = '—';
				row.appendChild(label);
				row.appendChild(value);
				card.appendChild(row);
			});
			gui.obj.jointCards.appendChild(card);
		});
	},

	updateCharts: function () {
		let recent = telemetryLog.records.slice(-60);
		[
			{ id: 'voltage-path', key: 'voltageV' },
			{ id: 'power-path', key: 'powerW' },
		].forEach(function (chart) {
			let path = G(chart.id);
			let values = recent.map(function (record) {
				return record[chart.key];
			});
			let valid = values.filter(Number.isFinite);
			if (valid.length < 2) {
				path.setAttribute('d', '');
				return;
			}
			let min = Math.min.apply(null, valid);
			let max = Math.max.apply(null, valid);
			if (max === min) {
				min -= 1;
				max += 1;
			}
			let points = [];
			values.forEach(function (value, index) {
				if (!Number.isFinite(value)) return;
				let x = values.length < 2 ? 0 : index * 600 / (values.length - 1);
				let y = 80 - (value - min) * 70 / (max - min);
				points.push((points.length ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1));
			});
			path.setAttribute('d', points.join(' '));
		});
	},

	initLogging: function () {
		try {
			let stored = localStorage.getItem(logStorageKey);
			if (stored !== null) {
				let parsed = JSON.parse(stored);
				if (!Array.isArray(parsed) || parsed.length > 1000 ||
					!parsed.every(function (record) {
						return record && typeof record === 'object' &&
							typeof record.timestamp === 'string' &&
							Array.isArray(record.jointsDeg) && record.jointsDeg.length === 12;
					})) {
					throw new Error('저장된 로그 형식이 올바르지 않습니다');
				}
				telemetryLog.records = parsed;
				if (parsed.length && Number.isFinite(parsed[parsed.length - 1].energyWh)) {
					telemetryLog.energyWh = parsed[parsed.length - 1].energyWh;
				}
			}
			gui.updateLogStatus('브라우저 로컬 저장소에 로그를 기록합니다.');
			gui.updateCharts();
		} catch (error) {
			telemetryLog.available = false;
			gui.updateLogStatus('로컬 로그를 읽지 못했습니다: ' + error.message);
		}
	},

	updateLogStatus: function (message) {
		if (!gui.obj.logStatus) return;
		gui.obj.logStatus.textContent = message + ' 저장된 샘플: ' + telemetryLog.records.length;
	},

	updateTelemetry: function () {
		let fresh = (demoMode || ws.status) && telemetry.lastSampleAt !== null &&
			monotonicNow() - telemetry.lastSampleAt <= 3000;
		let powerReady = fresh && telemetry.version === 2 && (telemetry.sensorStatus & 12) === 12;
		let imuValid = fresh && telemetry.version === 2 && (telemetry.sensorStatus & 2) !== 0;

		G('voltage').textContent = powerReady ? telemetry.voltage.toFixed(3) + ' V' : '—';
		G('current').textContent = powerReady ? telemetry.current.toFixed(3) + ' A' : '—';
		G('power').textContent = powerReady ? (telemetry.voltage * telemetry.current).toFixed(3) + ' W' : '—';
		G('energy').textContent = powerReady ? telemetryLog.energyWh.toFixed(5) + ' Wh' : '—';
		for (let i = 0; i < 3; i++) {
			G(['imu-x', 'imu-y', 'imu-z'][i]).textContent =
				imuValid ? (telemetry.imuAxes[i] / 100).toFixed(2) + '°' : '—';
		}

		telemetry.joints.forEach(function (angle, index) {
			let valueElement = G('joint-' + index);
			if (angle === null || !fresh) {
				valueElement.textContent = '—';
			} else {
				valueElement.textContent = angle.toFixed(2) + '°';
			}
		});

		document.querySelectorAll('[data-joint]').forEach(function (joint) {
			let angle = telemetry.joints[Number(joint.getAttribute('data-joint'))];
			if (angle === null || !fresh) {
				joint.style.fill = '#52646b';
				return;
			}
			let hue = Math.round(180 - Math.max(0, Math.min(180, angle)) * 0.5);
			joint.style.fill = 'hsl(' + hue + ', 70%, 58%)';
		});
	},

	recordTelemetry: function () {
		if (demoMode) {
			let power = telemetry.voltage * telemetry.current;
			if (telemetryLog.lastSampleAt !== null) {
				let elapsed = monotonicNow() - telemetryLog.lastSampleAt;
				if (elapsed > 0 && elapsed <= 2500) {
					telemetryLog.energyWh +=
						(telemetryLog.lastPower + power) * 0.5 * elapsed / 3600000;
				}
			}
			telemetryLog.lastSampleAt = monotonicNow();
			telemetryLog.lastPower = power;
			telemetryLog.records.push({
				timestamp: new Date().toISOString(),
				voltageV: telemetry.voltage,
				powerW: power,
				energyWh: telemetryLog.energyWh,
			});
			if (telemetryLog.records.length > 30) telemetryLog.records.shift();
			gui.updateCharts();
			return;
		}

		let timestamp = Date.now();
		let now = monotonicNow();
		let powerReady = telemetry.version === 1 ||
			(telemetry.version === 2 && (telemetry.sensorStatus & 12) === 12);
		let imuValid = telemetry.version === 2 && (telemetry.sensorStatus & 2) !== 0;
		let power = powerReady ? telemetry.voltage * telemetry.current : null;
		if (powerReady) {
			if (telemetryLog.lastSampleAt !== null) {
				let elapsed = now - telemetryLog.lastSampleAt;
				if (elapsed > 0 && elapsed <= 2500) {
					telemetryLog.energyWh +=
						(telemetryLog.lastPower + power) * 0.5 * elapsed / 3600000;
				}
			}
			telemetryLog.lastSampleAt = now;
			telemetryLog.lastPower = power;
		} else {
			telemetryLog.lastSampleAt = null;
			telemetryLog.lastPower = null;
		}

		let record = {
			timestamp: new Date(timestamp).toISOString(),
			sensorStatus: telemetry.sensorStatus,
			imuXDeg: !imuValid || telemetry.imuAxes[0] === null ? null : telemetry.imuAxes[0] / 100,
			imuYDeg: !imuValid || telemetry.imuAxes[1] === null ? null : telemetry.imuAxes[1] / 100,
			imuZDeg: !imuValid || telemetry.imuAxes[2] === null ? null : telemetry.imuAxes[2] / 100,
			jointsDeg: telemetry.joints.slice(),
			voltageV: powerReady ? telemetry.voltage : null,
			currentA: powerReady ? telemetry.current : null,
			powerW: power,
			energyWh: telemetryLog.energyWh,
		};
		telemetryLog.records.push(record);
		if (telemetryLog.records.length > 1000) telemetryLog.records.shift();
		gui.updateCharts();

		if (!telemetryLog.available) {
			gui.updateLogStatus('저장소 오류로 기록이 일시적으로만 유지됩니다.');
			return;
		}
		try {
			localStorage.setItem(logStorageKey, JSON.stringify(telemetryLog.records));
			gui.updateLogStatus('브라우저 로컬 저장소에 로그를 기록합니다.');
		} catch (error) {
			telemetryLog.available = false;
			gui.updateLogStatus('로컬 로그 저장 실패: ' + error.message);
		}
	},

	exportLog: function () {
		if (demoMode) return;
		if (!telemetryLog.records.length) {
			gui.updateLogStatus('내려받을 로그가 없습니다.');
			return;
		}
		let headers = ['timestamp', 'sensorStatus', 'imuXDeg', 'imuYDeg', 'imuZDeg'];
		for (let i = 0; i < 12; i++) headers.push('joint' + i + 'Deg');
		headers.push('voltageV', 'currentA', 'powerW', 'energyWh');
		let rows = [headers.join(',')];
		telemetryLog.records.forEach(function (record) {
			let values = [
				record.timestamp, record.sensorStatus, record.imuXDeg, record.imuYDeg, record.imuZDeg
			].concat(record.jointsDeg, [
				record.voltageV, record.currentA, record.powerW, record.energyWh
			]);
			rows.push(values.map(function (value) {
				return value === null || value === undefined ? '' : String(value);
			}).join(','));
		});
		let blob = new Blob(['\uFEFF' + rows.join('\r\n')], { type: 'text/csv;charset=utf-8' });
		let url = URL.createObjectURL(blob);
		let link = document.createElement('a');
		link.href = url;
		link.download = 'robot-dog-telemetry-' + new Date().toISOString().replace(/[:.]/g, '-') + '.csv';
		link.click();
		URL.revokeObjectURL(url);
	},

	clearLog: function () {
		if (demoMode) return;
		if (!window.confirm('이 브라우저에 저장된 로봇 텔레메트리 로그를 모두 삭제할까요?')) return;
		try {
			localStorage.removeItem(logStorageKey);
			telemetryLog.records = [];
			telemetryLog.energyWh = 0;
			telemetryLog.lastSampleAt = null;
			telemetryLog.lastPower = null;
			gui.updateLogStatus('로컬 로그를 삭제했습니다.');
		} catch (error) {
			gui.updateLogStatus('로컬 로그 삭제 실패: ' + error.message);
		}
	},

	onVisibilityChange: function () {
		if (document.visibilityState == "hidden") {
			ws.stop();
		}
	},

	displayNumber: function (n) {
		if (n == 0) {
			return "0.000";
		}

		return (n + "000000").slice(0, 5);
	},

	updateStatus: function (){
		if (demoMode) {
			gui.obj.status.textContent = '모의 미리보기 | 시뮬레이션 데이터 · ESP32 연결 안 됨';
			return;
		}
		if (!ws.status) {
			gui.obj.status.textContent = 'ESP32 연결 끊김';
			return;
		}

		if (telemetry.lastSampleAt === null || monotonicNow() - telemetry.lastSampleAt > 3000) {
			gui.obj.status.textContent = '연결됨 | 텔레메트리 대기 중 또는 데이터가 오래됨';
			return;
		}

		if (telemetry.version !== 1 && telemetry.version !== 2) {
			gui.obj.status.textContent = '지원하지 않는 텔레메트리 버전';
			return;
		}

		let parts = ['연결됨'];
		if (telemetry.version === 1) {
			parts.push('레거시 텔레메트리');
		} else {
			parts.push((telemetry.sensorStatus & 16) ? '보행 활성화' : '보행 비활성');
			parts.push((telemetry.sensorStatus & 4) ?
				((telemetry.sensorStatus & 8) ? 'INA219 측정 유효' : 'INA219 측정 오래됨/무효') :
				'INA219 미연결');
			parts.push((telemetry.sensorStatus & 2) ? 'MPU9250 측정 유효' :
				((telemetry.sensorStatus & 1) ? 'MPU9250 초기화됨' : 'MPU9250 미연결'));
		}
		if (telemetry.version === 1 || (telemetry.sensorStatus & 4)) {
			parts.push(telemetry.voltage.toFixed(3) + ' V');
			parts.push(telemetry.current.toFixed(3) + ' A');
			parts.push((telemetry.voltage * telemetry.current).toFixed(3) + ' W');
		}
		if (telemetry.version === 2 && telemetry.loopTime !== null) {
			parts.push('루프 ' + telemetry.loopTime + ' µs');
		}
		gui.obj.status.textContent = parts.join(' | ');
	},
};

let ws = {
	ws:                null,
	status:            false,
	armed:             false,
	pendingArmSequence: null,
	nextArmSequence:   0,
	armTimeout:        null,
	reconnectTimeout:  null,
	reconnectAttempt:  0,
	controlMessage:    '',
	error:             false,
	updateInterval:    null,
	telemetryInterval: null,
	
	init: function () {
		clearInterval(ws.updateInterval);
		clearInterval(ws.telemetryInterval);
		clearTimeout(ws.reconnectTimeout);
		ws.reconnectTimeout = null;
		ws.reconnectAttempt = 0;
		if (demoMode) {
			ws.status = false;
			ws.disarmLocal();
			ws.controlMessage = '모의 미리보기 | 로봇 제어 연결 없음';
			demo.start();
			gui.updateControlState();
			return;
		}
		ws.connect();
	},

	connect: function () {
		try {
			let socket = new WebSocket(c.w);
			ws.ws = socket;
			socket.onopen = function() {
				if (ws.ws !== socket) return;
				ws.status = true;
				ws.disarmLocal();
				ws.controlMessage = '';
				ws.error = false;
				ws.reconnectAttempt = 0;
				clearInterval(ws.updateInterval);
				clearInterval(ws.telemetryInterval);
				ws.updateInterval = setInterval(ws.update, 50);
				ws.telemetryInterval = setInterval(ws.telemetryRequest, 1000);
				gui.updateControlState();
			};
			socket.onerror = function (event) {
				if (ws.ws !== socket) return;
				ws.handleDisconnect('WebSocket 오류; 기기 감시 정지를 기다립니다.', event);
			};
			socket.onclose = function() {
				if (ws.ws !== socket) return;
				ws.handleDisconnect('WebSocket 연결 종료; 기기 감시 정지를 기다립니다.');
				ws.scheduleReconnect();
			};
			socket.onmessage = function (event) {
				if (ws.ws !== socket) return;
				if (typeof event.data === 'string') {
					ws.handleControlMessage(event.data);
					return;
				}
				event.data.arrayBuffer().then(buffer => {
					if (ws.ws === socket && ws.status) ws.parseEvent(new Uint8Array(buffer));
				}).catch(error => {
					if (ws.ws === socket) console.error('WebSocket message decode failed:', error);
				});
			};
		} catch(e) {
			ws.handleDisconnect('WebSocket 연결을 시작하지 못했습니다.', e);
			ws.scheduleReconnect();
		};
	},

	scheduleReconnect: function () {
		if (ws.reconnectTimeout !== null) return;
		let delay = Math.min(1000 * Math.pow(2, ws.reconnectAttempt), 30000);
		ws.reconnectAttempt = Math.min(ws.reconnectAttempt + 1, 5);
		ws.controlMessage = (ws.controlMessage || 'WebSocket 연결 끊김.') + ' ' +
			Math.ceil(delay / 1000) + '초 후 재연결합니다. 자동 보행 재개는 되지 않습니다.';
		ws.reconnectTimeout = setTimeout(function () {
			ws.reconnectTimeout = null;
			ws.connect();
		}, delay);
		gui.updateControlState();
	},

	update: function(data) {
		if (ws.status && ws.armed && ws.ws && ws.ws.readyState === 1) {
			ws.sendPacket(packet.move());
		}
	},

	sendPacket: function (data) {
		if (!ws.ws || ws.ws.readyState !== 1) {
			ws.handleDisconnect('WebSocket 연결이 닫혀 패킷을 보내지 못했습니다; 기기 감시 정지를 기다립니다.');
			ws.scheduleReconnect();
			return false;
		}
		try {
			ws.ws.send(data);
			return true;
		} catch (error) {
			ws.handleDisconnect('WebSocket 송신 실패; 기기 감시 정지를 기다립니다.', error);
			return false;
		}
	},

	handleDisconnect: function (message, error) {
		ws.status = false;
		ws.disarmLocal();
		ws.controlMessage = message;
		telemetry.lastSampleAt = null;
		telemetryLog.lastSampleAt = null;
		telemetryLog.lastPower = null;
		clearInterval(ws.updateInterval);
		clearInterval(ws.telemetryInterval);
		if (error) {
			ws.error = true;
			console.error('WebSocket connection failed:', error);
		}
		gui.updateControlState();
	},

	arm: function () {
		if (!gui.armEligible() || ws.armed || ws.pendingArmSequence !== null ||
			!ws.ws || ws.ws.readyState !== 1) return;
		failsafe.setFS();
		ws.nextArmSequence = (ws.nextArmSequence + 1) & 255;
		ws.pendingArmSequence = ws.nextArmSequence;
		ws.controlMessage = '';
		if (!ws.sendPacket(new Uint8Array([65, ws.pendingArmSequence]))) return;
		ws.armTimeout = setTimeout(function () {
			if (ws.pendingArmSequence === ws.nextArmSequence) {
				ws.disarmLocal();
				ws.controlMessage = 'ARM 확인 시간 초과; STOP을 전송했습니다.';
				ws.sendPacket(new Uint8Array([83]));
				gui.updateControlState();
			}
		}, 500);
		gui.updateControlState();
	},

	stop: function () {
		ws.disarmLocal();
		if (demoMode) {
			ws.controlMessage = '모의 미리보기 | 로봇 제어 연결 없음';
			gui.updateControlState();
			return;
		}
		ws.controlMessage = '';
		if (!ws.sendPacket(new Uint8Array([83])) && !ws.controlMessage) {
			ws.controlMessage = '연결이 없어 STOP을 전송하지 못했습니다; 기기 감시 정지를 기다립니다.';
		}
		gui.updateControlState();
	},

	disarmLocal: function () {
		failsafe.setFS();
		ws.armed = false;
		ws.pendingArmSequence = null;
		if (ws.armTimeout !== null) {
			clearTimeout(ws.armTimeout);
			ws.armTimeout = null;
		}
	},

	handleControlMessage: function (message) {
		let armed = /^ARMED:(\d+)$/.exec(message);
		if (armed && ws.pendingArmSequence === Number(armed[1])) {
			if (ws.armTimeout !== null) clearTimeout(ws.armTimeout);
			ws.armTimeout = null;
			ws.pendingArmSequence = null;
			ws.armed = true;
			ws.controlMessage = '';
			gui.updateControlState();
			ws.update();
			return;
		}
		let rejected = /^ARM_REJECTED:(\d+)$/.exec(message);
		if (rejected && ws.pendingArmSequence === Number(rejected[1])) {
			ws.disarmLocal();
			ws.controlMessage = '기기에서 ARM 거부: MPU9250 샘플을 확인하세요.';
			gui.updateControlState();
		}
	},

	parseEvent: function (binaryData) {
		if (binaryData.length < 1) {
			return;
		}

		switch (binaryData[0]) {
			case 84:
				ws.telemetryResponse(binaryData);
				break;
		}
	},

	telemetryRequest: function (data) {
		if (ws.status) {
			ws.sendPacket(packet.telemetry());
		}
	},

	telemetryResponse: function (binaryData) {
		packet.telemetryParse(binaryData);
	}
};

let demo = {
	interval: null,
	tick: 0,

	start: function () {
		clearInterval(demo.interval);
		demo.update();
		demo.interval = setInterval(demo.update, 250);
	},

	update: function () {
		demo.tick++;
		let phase = demo.tick / 8;
		let frame = new Uint8Array(40);
		let view = new DataView(frame.buffer);
		frame[0] = 84;
		frame[1] = 2;
		view.setUint16(2, 15);
		view.setInt16(4, Math.round(Math.sin(phase) * 850));
		view.setInt16(6, Math.round(Math.cos(phase) * 550));
		view.setInt16(8, Math.round(Math.sin(phase / 2) * 300));
		for (let index = 0; index < 12; index++) {
			let offset = 10 + index * 2;
			view.setUint16(offset, Math.round(9000 + Math.sin(phase + index / 3) * 1800));
		}
		view.setUint16(34, 12000 + Math.round(Math.sin(phase / 4) * 120));
		view.setInt16(36, 650 + Math.round((1 + Math.sin(phase)) * 220));
		view.setUint16(38, 400);
		packet.telemetryParse(frame);
		gui.update();
	},
};

let failsafe = {
	setFS: function () {
		vector.move.x       = 0;
		vector.move.y       = 0;
		vector.move.z       = 0;
		vector.rotate.roll  = 0;
		vector.rotate.pitch = 0;
		vector.rotate.yaw   = 0;
	}
};


let packet = {
	init: function () {
		packet.pMove = new ArrayBuffer(14);
		packet.vMove = new Uint8Array(packet.pMove);
	},
	_norm1: function (value) {
		return (value+1)*10000;
	},
	_uint16: function (view, num, offset) {
		view[offset]   = (num>>8)&255;
		view[offset+1] = num&255;
	},
	_getUint16: function (data, offset) {
		return parseInt(data[offset]<<8 | data[offset+1]);
	},
	_getInt16: function (data, offset) {
		let value = packet._getUint16(data, offset);
		return value & 0x8000 ? value - 0x10000 : value;
	},
	move: function () {
		packet.vMove[0] = 77;
		packet.vMove[1] = 1;
		packet._uint16(packet.vMove, packet._norm1(vector.move.x),        2);
		packet._uint16(packet.vMove, packet._norm1(vector.move.y),        4);
		packet._uint16(packet.vMove, packet._norm1(vector.move.z),        6);
		packet._uint16(packet.vMove, packet._norm1(vector.rotate.pitch),  8);
		packet._uint16(packet.vMove, packet._norm1(vector.rotate.roll),  10);
		packet._uint16(packet.vMove, packet._norm1(vector.rotate.yaw),   12);

		return packet.pMove;
	},
	telemetry: function () {
		return new Uint8Array([84, 1]);
	},
	telemetryParse: function (binaryTelemetry) {
		if (binaryTelemetry.length < 2) {
			telemetry.version = null;
			telemetry.lastSampleAt = null;
			if (ws.armed) ws.stop();
			return;
		}

		if (binaryTelemetry[1] === 1 && binaryTelemetry.length >= 14) {
			telemetry.version = 1;
			telemetry.voltage = packet._getUint16(binaryTelemetry, 4) / 1000;
			telemetry.current = packet._getUint16(binaryTelemetry, 6) / 1000;
			telemetry.loopTime = packet._getUint16(binaryTelemetry, 8);
			telemetry.sensorStatus = 0;
			telemetry.imuAxes = [null, null, null];
			telemetry.joints = Array(12).fill(null);
			telemetry.lastSampleAt = monotonicNow();
			if (ws.armed) ws.stop();
			gui.recordTelemetry();
			return;
		}

		if (binaryTelemetry[1] !== 2 || binaryTelemetry.length !== 40) {
			telemetry.version = binaryTelemetry[1] === undefined ? null : binaryTelemetry[1];
			telemetry.lastSampleAt = null;
			if (ws.armed) ws.stop();
			return;
		}

		telemetry.version = binaryTelemetry[1];
		telemetry.sensorStatus = packet._getUint16(binaryTelemetry, 2);
		telemetry.imuAxes = [
			packet._getInt16(binaryTelemetry, 4),
			packet._getInt16(binaryTelemetry, 6),
			packet._getInt16(binaryTelemetry, 8),
		];
		telemetry.joints = [];
		for (let i = 0; i < 12; i++) {
			telemetry.joints.push(packet._getUint16(binaryTelemetry, 10 + i * 2) / 100);
		}
		telemetry.voltage = packet._getUint16(binaryTelemetry, 34) / 1000;
		telemetry.current = packet._getInt16(binaryTelemetry, 36) / 1000;
		telemetry.loopTime = packet._getUint16(binaryTelemetry, 38);
		telemetry.lastSampleAt = monotonicNow();
		if (ws.armed && (telemetry.sensorStatus & 16) === 0) ws.disarmLocal();
		gui.recordTelemetry();
	}
}

class onScreenGamepad {
	constructor(obj, deadband, callback) {
		this.obj = obj,
		this.deadband = deadband || 0.05,
		this.callback = callback;
		
		this.isEvent = false;
		this.vector = {
				x: 0.0,
				y: 0.0,
			};
	}
	
	init () {
		this.obj.addEventListener('mousedown',   (event) => this.eventStart(event));
		this.obj.addEventListener('touchstart',  (event) => this.eventStart(event));
		
		this.obj.addEventListener('mouseup',     (event) => this.eventFinish(event));
		this.obj.addEventListener('mouseout',    (event) => this.eventFinish(event));
		this.obj.addEventListener('mouseleave',  (event) => this.eventFinish(event));
		this.obj.addEventListener('touchend',    (event) => this.eventFinish(event));
		this.obj.addEventListener('touchcancel', (event) => this.eventFinish(event));
		
		this.obj.addEventListener('mousemove',   (event) => this.eventMove(false, event));
		this.obj.addEventListener('touchmove',   (event) => this.eventMove(true, event));
	}
	
	eventStart() {
		if (!ws.armed) return;
		this.isEvent = true;
		this.obj.classList.add('active');
	}
	
	eventFinish() {
		this.isEvent = false;
		this.vector.x = 0.0;
		this.vector.y = 0.0;
		this.display(0, 0);
		this.obj.classList.remove('active');
		
		this.callback(this.vector);
	}
	
	eventMove(isTouch, event) {
		if (!ws.armed) {
			this.vector.x = 0;
			this.vector.y = 0;
			return;
		}
		let sendEvent = false;
		let x = ((isTouch ? event.targetTouches[0].clientX : event.clientX) - event.target.offsetLeft) / this.obj.offsetWidth*2-1;
		let y = ((isTouch ? event.targetTouches[0].clientY : event.clientY) - event.target.offsetTop) / this.obj.offsetHeight*2-1;
		if (Math.abs(x) <= this.deadband) x = 0;
		if (Math.abs(y) <= this.deadband) y = 0;
		if (x > 1) x = 1;
		if (x < -1) x =-1;
		if (y > 1) y = 1;
		if (y < -1) y =-1;
		if (!this.isEvent) {
			x = 0;
			y = 0;
		}
		if (this.vector.x !== x || this.vector.y !== y) {
			sendEvent = true;
		}
		this.vector.x =  x;
		this.vector.y = -y;
		
		if (sendEvent) {
			this.display(x, y);
			this.callback(this.vector);
		}
	}
	
	display(x, y) {
		this.obj.style.backgroundPosition = (x+1)/2*100 + '% ' + (y+1)/2*100 + '%';
	}
};

let control = {
	init() {
		let leftJ  = new onScreenGamepad(G('leftJ'),  0.05, this.leftJcallback);
		let rightJ = new onScreenGamepad(G('rightJ'), 0.05, this.rightJcallback);
		leftJ.init();
		rightJ.init();
	},
	leftJcallback(v) {
		vector.rotate.yaw = v.x;
	},
	rightJcallback(v) {
		/**
		 * Body rotation, this is temporary to easy validate IK
		 */
		if (gui.obj.body_rotate.checked) {
			vector.rotate.roll  = v.y;
			vector.rotate.pitch = v.x;
			
			return;
		}
		vector.move.x = v.x;
		vector.move.y = v.y;
	}
}

control.init();
gui.init();
packet.init();
ws.init();
