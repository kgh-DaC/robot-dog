// Runs the control page script (robot_control_web/index.html) in a fake
// browser: fake DOM, fake clock/timers and a scripted fetch. Prints a JSON
// report for tests/test_control_page.py.
// Usage: node page_harness.js <index.html> [gateway|file|file-noserial|framed|hub]
//   default: page served by the robot; "gateway": page served by the USB
//   gateway (http://127.0.0.1:8787), connection mode switching; "file": page
//   opened from disk (file://) with a fake Web Serial port and fake firmware;
//   "file-noserial": the same in a browser without Web Serial; "framed": the
//   robot's page shown inside the local page's Wi-Fi frame.
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const MODE = process.argv[3] || "robot";
let now = 0;
const timers = [];
function addTimer(fn, ms, repeat) { const t = { fn, ms, next: now + ms, repeat, dead: false }; timers.push(t); return t; }

const elements = {};
function el(id) {
  if (!elements[id]) {
    elements[id] = { id, textContent: "", className: "", disabled: false, hidden: true, innerHTML: "",
      dataset: {}, classList: { toggle() {} }, onclick: null, style: {},
      firstChild: { textContent: "" }, lastChild: { textContent: "" } };
  }
  return elements[id];
}
const dirButtons = ["FORWARD", "LEFT", "STOP", "RIGHT", "BACKWARD"].map((d) => {
  const b = el("dir_" + d); b.dataset.dir = d; return b;
});

// Canvas stub: counts drawing calls so the test can see the chart was drawn.
const draw = { lineTo: 0, arc: 0 };
Object.assign(el("lvChart"), {
  width: 0, height: 0,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 200 }),
  getContext: () => new Proxy({}, { get: (_, name) => (name in draw ? () => { draw[name]++; } : () => {}),
                                   set: () => true }),
});

// Stubs for the joint model and the ON/OFF comparison charts.
const draw2 = { mdCanvas: 0, cmpRoll: 0, cmpPitch: 0 };
Object.keys(draw2).forEach((id) => Object.assign(el(id), {
  width: 0, height: 0,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 300 }),
  getContext: () => new Proxy({}, { get: (_, name) => (name === "lineTo" ? () => { draw2[id]++; } : () => {}),
                                   set: () => true }),
}));

let statusMode = "ok";       // ok | fail | hang
// Overrides for the model and comparison scenarios (null = default value).
const sim = { levelEnabled: true, levelActive: true, roll: null, pitch: null, walk: "FORWARD", armed: true, gamma: 90, trim: null };
let imuMapped = true;
const posts = [];
const statusUrls = [];
let held = null, heldPost = null;             // gateway: a held USB status reply, released by the test
const GW_WIFI = { ssid: "RobotDog-ABCD", password: "12345678",
  sta: { status: "CONNECTED", ip: "172.20.10.3", ssid: "민수의 iPhone", host: "robotdog.local" } };
const okStatus = () => ({
  link: "wifi", connected: true, armed: sim.armed, controller: "WIFI", youControl: true, walk: sim.walk,
  homing: false, settled: false, resetReason: "POWERON", lastStop: "boot", uptimeMs: 1, leaseTimeoutMs: 1000,
  angles: { LF: [90, 45, sim.gamma], RF: [90, 45, 90], LH: [90, 45, 90], RH: [90, 45, 90] }, wifi: { ssid: "x", clients: 1 },
  imu: { ready: true, chip: "MPU_COMPATIBLE", whoAmI: "0x75", address: "0x69", valid: true, ageMs: 10,
    accG: { x: 0, y: 0, z: 1 }, axisDeg: { x: 2.5, y: -1.25 }, zeroed: true,
    rollMap: imuMapped ? "+X" : "NONE", pitchMap: imuMapped ? "-Y" : "NONE",
    rollDeg: imuMapped ? (sim.roll === null ? 2.5 : sim.roll) : null,
    pitchDeg: imuMapped ? (sim.pitch === null ? 1.25 : sim.pitch) : null },
  level: { enabled: sim.levelEnabled, active: sim.levelActive, sensorUsable: true, rollCompDeg: 1.5, pitchCompDeg: -0.5,
    ...(sim.noOffsets ? {} : { gammaOffsetDeg: { LF: -2, RF: 1, LH: -1, RH: 2 } }), maxCompDeg: 6, tiltLimitDeg: 20 },
  ...(sim.trim ? { standTrimDeg: sim.trim } : {}),     // older firmware: field absent
});
const response = (obj) => ({ json: () => Promise.resolve(obj), status: 200 });
// Gateway page: relative URLs go to the USB gateway, absolute ones to the robot's IP.
function statusFor(url) {
  const s = okStatus();
  if (MODE !== "gateway") return s;
  const hot = url.startsWith("http://");
  return Object.assign(s, hot ? { link: "wifi", controller: "WIFI" }
    : { link: "usb", controller: "SERIAL", serialPort: "COM14", wifi: GW_WIFI });
}

const context = {
  document: {
    hidden: false,
    getElementById: el,
    querySelectorAll: () => dirButtons,
    addEventListener() {},
  },
  window: {
    addEventListener() {},
    AbortController: function () {
      this.signal = { onabort: null };
      this.abort = () => { if (this.signal.onabort) this.signal.onabort(); };
    },
  },
  crypto: { getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = i; return a; } },
  URLSearchParams,
  Date: { now: () => now },
  getComputedStyle: () => ({ getPropertyValue: () => "#000" }),
  setInterval: (fn, ms) => addTimer(fn, ms, true),
  setTimeout: (fn, ms) => addTimer(fn, ms, false),
  clearTimeout: (t) => { if (t) t.dead = true; },
  fetch(url, opts) {
    if (opts && opts.method === "POST") {
      posts.push({ t: now, body: opts.body, url });
      if (sim.holdPost) return new Promise((resolve) => { heldPost = () => resolve(response({ ok: true })); });
      return Promise.resolve(response({ ok: true }));
    }
    statusUrls.push(url);
    if (sim.holdUsb && !url.startsWith("http://")) {   // ignores abort: tests the stale-reply guard
      return new Promise((resolve) => { held = () => resolve(response(statusFor(url))); });
    }
    if (statusMode === "fail") return Promise.reject(new Error("network"));
    if (statusMode === "hang") {
      return new Promise((_, reject) => {
        if (opts && opts.signal) opts.signal.onabort = () => reject(new Error("aborted"));
      });
    }
    return Promise.resolve(response(statusFor(url)));
  },
};
if (MODE === "gateway") {
  const ls = {};
  context.location = { protocol: "http:", host: "127.0.0.1:8787" };
  context.window.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); } };
  context.__ls = ls;
}
// ---- page opened from disk: fake firmware behind a fake Web Serial port -------
const fw = { armed: false, ctrl: "NONE", walk: "STOPPED", lease: 0, stop: "boot", autoPhase: "IDLE", silent: false,
  log: [], openCount: 0, closeCount: 0, requestCount: 0, granted: false, opened: false, busy: false };
function fwHandle(line) {
  const expire = () => { if (fw.armed && now > fw.lease) { fw.armed = false; fw.ctrl = "NONE"; fw.walk = "STOPPED"; fw.stop = "heartbeat_timeout"; } };
  expire();
  const parts = line.split(" ");
  switch (parts[0]) {
    case "HELLO": return ["READY SERVO_POSE_V1"];
    case "CAPS": return ["CAPS SERVO_POSE_V1 WALK4 LEASE1 WIFI1 IMU1 LEVEL1 STA1 TRIM1 AUTOWALK1"];
    case "STATE": return ["EVENT NOISE", `STATE armed=${fw.armed ? 1 : 0} ctrl=${fw.ctrl} walk=${fw.walk} homing=0 settled=1 reset=BROWNOUT stop=${fw.stop} uptime=${now}`];
    case "STATUS": return ["STATUS LF=90/45/90 RF=90/45/90 LH=90/45/90 RH=86/45/90"];
    case "IMU": return ["IMU ready=1 chip=MPU_COMPATIBLE who=0x75 addr=0x68 valid=1 age=5 ax=0.00 ay=0.00 az=1.00 x=1.50 y=-0.50 zeroed=1 rollmap=-Y pitchmap=+X roll=0.50 pitch=1.50"];
    case "LEVEL":
      if (parts[1]) return ["OK LEVEL " + parts[1]];
      return ["LEVEL enabled=0 active=0 sensor=1 rollcomp=0.00 pitchcomp=0.00 offsets=0.0/0.0/0.0/0.0 max=6.0 limit=20.0 trim=9/1/-1/-9"];
    case "AUTOWALK":
      if (parts[1]) return ["OK AUTOWALK " + parts[1]];
      return [`AUTOWALK enabled=1 phase=${fw.autoPhase} delay=5000 run=10000 blocked=0`];
    case "WIFI": return ["WIFI ssid=RobotDog-0FA1 pass=12345678 ip=192.168.4.1 clients=0 sta=CONNECTED sta_ip=172.20.10.3 sta_ssid=My Phone=1"];
    case "ARM": fw.armed = true; fw.ctrl = "SERIAL"; fw.lease = now + 1000; return ["OK ARM"];
    case "HB": if (!fw.armed) return ["ERR not_armed"]; fw.lease = now + 1000; return ["OK HB"];
    case "WALKHB": if (!fw.armed) return ["ERR not_armed"]; fw.lease = now + 1000; fw.walk = parts[1] === "STOP" ? "STOPPED" : parts[1]; return ["OK WALKHB " + parts[1]];
    case "WALK": fw.armed = false; fw.ctrl = "NONE"; fw.walk = "STOPPED"; fw.stop = "serial_stop"; fw.autoPhase = "IDLE"; return ["OK WALK STOPPED"];
    case "DISARM": fw.armed = false; fw.ctrl = "NONE"; fw.walk = "STOPPED"; return ["OK DISARM"];
    case "SETTLED": return ["SETTLED 1"];
    case "WIFI_SET": return ["OK WIFI_SET"];
    default: return ["ERR unknown_command"];
  }
}
function makeFakePort() {
  let waiting = null, failRead = null;
  const queue = [];
  const push = (text) => {
    const chunk = Buffer.from(text, "utf8");
    if (waiting) { const give = waiting; waiting = null; give({ value: chunk, done: false }); } else queue.push(chunk);
  };
  return {
    unplug() { fw.opened = false; if (failRead) failRead(new Error("device lost")); },
    open: async () => { if (fw.busy) throw new Error("port busy"); fw.opened = true; fw.openCount++; fw.granted = true; queue.length = 0; },
    close: async () => { fw.opened = false; fw.closeCount++; },
    readable: { getReader: () => ({
      read: () => (queue.length ? Promise.resolve({ value: queue.shift(), done: false })
        : new Promise((resolve, reject) => { waiting = resolve; failRead = reject; })),
      cancel: async () => { if (waiting) { const give = waiting; waiting = null; give({ done: true }); } },
      releaseLock() {} }) },
    writable: { getWriter: () => ({
      write: async (bytes) => {
        if (!fw.opened) throw new Error("device lost");
        Buffer.from(bytes).toString("utf8").split("\r\n").filter(Boolean).forEach((line) => {
          fw.log.push({ t: now, line });
          if (!fw.silent) fwHandle(line).forEach((reply) => push(reply + "\r\n"));
        });
      },
      releaseLock() {} }) },
  };
}
if (MODE === "file" || MODE === "file-noserial") {
  const ls = {};
  const fakePort = makeFakePort();
  context.__port = fakePort;
  context.location = { protocol: "file:", host: "" };
  context.window.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); } };
  context.TextEncoder = TextEncoder;
  context.TextDecoder = TextDecoder;
  context.navigator = MODE === "file" ? { serial: {
    getPorts: async () => (fw.granted ? [fakePort] : []),
    requestPort: async () => { fw.requestCount++; return fakePort; } } } : {};
}
if (MODE === "framed" || MODE === "hub") { context.window.top = {}; context.window.self = context.window; }
// "hub": the local robot hub (start_hub.bat, hub/serve.py on localhost:8800) shows this page in its frame.
if (MODE === "hub") {
  const ls = {};
  context.location = { protocol: "http:", host: "localhost:8800" };
  context.window.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); } };
}
context.AbortController = context.window.AbortController;  // window is the global in a browser
vm.createContext(context);

const flush = () => new Promise((r) => setImmediate(r));
async function advance(ms) {
  const end = now + ms;
  while (now < end) {
    now += 10;
    for (const t of timers.slice()) {
      if (t.dead || t.next > now) continue;
      if (t.repeat) t.next += t.ms; else t.dead = true;
      t.fn();
    }
    await flush(); await flush();
  }
}
const hbCount = () => posts.filter((p) => p.body.includes("cmd=HB")).length;
const snapshot = () => ({ armed: el("sArmed").textContent, walk: el("sWalk").textContent,
  joints: el("joints").innerHTML, link: el("linkText").textContent, arm: el("arm").disabled,
  fwd: dirButtons[0].disabled, roll: el("vRoll").textContent, pitch: el("vPitch").textContent,
  rollDir: el("vRollDir").textContent, rollComp: el("vRollComp").textContent, oLF: el("oLF").textContent,
  sensor: el("bSensor").lastChild.textContent, map: el("bMap").lastChild.textContent,
  level: el("bLevel").lastChild.textContent, toggle: el("lvToggle").textContent,
  rollLabel: el("kRollLabel").firstChild.textContent });

const lastUrl = () => statusUrls[statusUrls.length - 1];
const connView = () => ({ target: el("connTarget").textContent, note: el("connNote").textContent,
  msg: el("msg").textContent, link: el("linkText").textContent, url: lastUrl(),
  hsSetHidden: el("hsSet").hidden, hsState: el("hsState").textContent, addr: el("hsAddr").value,
  addrRowHidden: el("hsAddrRow").hidden, wifiCardHidden: el("wifiCard").hidden, wifiInfo: el("wifiInfo").textContent });

if (MODE === "gateway") (async () => {
  vm.runInContext(script, context);
  await advance(1500);
  const g = { initial: connView() };
  el("cmHot").onclick();                     // armed by this page: switching is refused
  await advance(300);
  g.refusedArmed = connView();
  sim.armed = false;
  await advance(600);
  el("hsSsid").value = "민수의 iPhone"; el("hsPass").value = "hotspot123";
  el("hsSave").onclick();
  await advance(300);
  const wifiSet = posts.filter((p) => p.body.includes("cmd=WIFI_SET"));
  g.wifiSet = { bodies: wifiSet.map((p) => p.body), url: wifiSet.length ? wifiSet[0].url : null, passAfter: el("hsPass").value };
  el("hsClear").onclick();
  await advance(300);
  g.wifiClear = posts.filter((p) => p.body.includes("cmd=WIFI_CLEAR")).map((p) => p.body);
  el("hsAddr").value = "evil.example/x?y"; el("hsAddr").oninput();
  el("hsGo").onclick();
  await advance(300);
  g.badAddr = connView();
  el("hsAddr").value = "http://172.20.10.3/";
  sim.holdUsb = true;
  await advance(300);                        // a USB status request is now in flight
  el("hsGo").onclick();
  sim.holdUsb = false;
  if (held) held();
  await flush(); await flush(); await flush();
  g.lateUsbReply = connView();               // the old target's reply must be ignored
  await advance(1000);
  g.hot = Object.assign(connView(), { stored: context.__ls["robotdog.conn"] });
  el("arm").onclick();
  await advance(100);
  g.armUrl = posts[posts.length - 1].url;
  el("cmUsb").onclick();
  await advance(600);
  g.back = Object.assign(connView(), { stored: context.__ls["robotdog.conn"] });
  // A command still waiting for its reply blocks a mode switch.
  sim.holdPost = true;
  el("arm").onclick();
  await advance(50);
  el("cmHot").onclick();
  await advance(50);
  g.busy = connView();
  sim.holdPost = false;
  heldPost();
  await advance(300);
  g.afterBusy = connView();
  // Reload with hotspot mode saved: the page starts on the robot's address.
  context.__ls["robotdog.conn"] = JSON.stringify({ mode: "hot", host: "robotdog.local" });
  timers.forEach((t) => { t.dead = true; });   // the old page instance is gone
  vm.runInContext(script, context);
  await advance(600);
  g.reloaded = connView();
  process.stdout.write(JSON.stringify(g));
})();

if (MODE === "framed" || MODE === "hub") (async () => {
  vm.runInContext(script, context);
  await advance(600);
  process.stdout.write(JSON.stringify({ connCardHidden: el("connCard").hidden, pinCardHidden: el("pinCard").hidden, armed: el("sArmed").textContent }));
})();

if (MODE === "file-noserial") (async () => {
  vm.runInContext(script, context);
  await advance(600);
  const view = { serialDisabled: el("cmSerial").disabled, note: el("connNote").textContent };
  el("cmSerial").onclick();
  await advance(600);
  view.msg = el("msg").textContent;
  el("hsGo").onclick();
  await advance(300);
  view.frame = el("wifiFrame").src;
  process.stdout.write(JSON.stringify(view));
})();

if (MODE === "file") (async () => {
  const sent = (from) => fw.log.slice(from).map((e) => e.line);
  const view = () => Object.assign(connView(), { armed: el("sArmed").textContent, walk: el("sWalk").textContent,
    fwd: dirButtons[0].disabled, arm: el("arm").disabled, opened: fw.opened, frame: el("wifiFrame").src,
    frameCardHidden: el("frameCard").hidden, localUiHidden: el("localUi").hidden, fetches: statusUrls.length + posts.length,
    serialRowHidden: el("serialRow").hidden, autoCardHidden: el("autoCard").hidden, joints: el("joints").innerHTML });
  vm.runInContext(script, context);
  const lines0 = draw2.mdCanvas;
  await advance(800);
  const f = { initial: Object.assign(view(), { mdLines: draw2.mdCanvas - lines0, bannerHidden: el("mdBanner").hidden,
    banner: el("mdBanner").textContent, pinBody: el("pinBody").innerHTML, pinSvg: el("pinSvg").innerHTML, pinCardHidden: el("pinCard").hidden, serialHidden: el("cmSerial").hidden, usbHidden: el("cmUsb").hidden,
    offHidden: el("cmOff").hidden, serialDisabled: el("cmSerial").disabled, opens: fw.openCount }) };
  el("arm").onclick();                       // not connected: refused, nothing is written
  await advance(200);
  f.armWhileOff = { msg: el("msg").textContent, written: fw.log.length };

  fw.autoPhase = "WAITING";                  // the board has just booted and is about to walk by itself
  el("cmSerial").onclick();
  await advance(1500);
  f.connected = Object.assign(view(), { bannerHidden: el("mdBanner").hidden, requests: fw.requestCount, autoPhase: fw.autoPhase, log: sent(0).slice(0, 4),
    stops: sent(0).filter((l) => l === "WALK STOP").length, fwArmed: fw.armed });

  el("arm").onclick();
  await advance(400);
  dirButtons[0].onclick();
  await advance(3000);                       // three lease periods: only heartbeats keep it walking
  f.walking = Object.assign(view(), { fwArmed: fw.armed, fwWalk: fw.walk, hb: sent(0).filter((l) => l === "HB").length });
  el("cmOff").onclick();                     // armed by this page: leaving is refused
  await advance(300);
  f.refusedArmed = Object.assign(view(), { fwArmed: fw.armed });

  let mark = fw.log.length;
  el("stop").onclick();
  await advance(400);
  f.stopped = Object.assign(view(), { fwArmed: fw.armed, stops: sent(mark).filter((l) => l === "WALK STOP").length });

  // Firmware stops answering while armed: status goes stale, heartbeats must stop.
  el("arm").onclick();
  await advance(400);
  f.rearmed = fw.armed;
  fw.silent = true;
  await advance(1000);
  mark = fw.log.length;
  await advance(3000);
  f.silent = Object.assign(view(), { hbAfterStale: sent(mark).filter((l) => l === "HB").length });
  fw.silent = false;
  await advance(3000);
  f.afterSilent = Object.assign(view(), { fwArmed: fw.armed });

  // Switch to Wi-Fi: the robot is stopped, the pose is confirmed, the port is closed, the frame opens.
  mark = fw.log.length;
  el("cmHot").onclick();
  await advance(1200);
  f.wifi = Object.assign(view(), { tail: sent(mark).filter((l) => l === "WALK STOP" || l === "SETTLED"), closes: fw.closeCount,
    stored: context.__ls });
  mark = fw.log.length;
  el("cmOff").onclick();
  await advance(600);
  f.off = Object.assign(view(), { written: fw.log.length - mark });

  el("cmSerial").onclick();                  // the port was allowed before: no chooser this time
  await advance(1500);
  f.reconnected = Object.assign(view(), { requests: fw.requestCount, opens: fw.openCount, fwArmed: fw.armed });
  context.__port.unplug();
  await advance(1500);
  f.unplugged = view();
  fw.busy = true;                            // another program holds the port
  el("cmSerial").onclick();
  await advance(800);
  f.busy = view();
  process.stdout.write(JSON.stringify(f));
})();

if (MODE === "robot") (async () => {
  vm.runInContext(script, context);
  await advance(2000);
  const report = { freshHb: hbCount(), fresh: snapshot(), chartLines: draw.lineTo };
  el("lvToggle").onclick();
  await advance(50);
  report.levelPost = posts.filter((p) => p.body.includes("cmd=LEVEL")).map((p) => p.body);
  imuMapped = false;
  await advance(1000);
  report.unmapped = snapshot();
  imuMapped = true;
  await advance(1000);
  for (const mode of ["fail", "hang"]) {
    statusMode = mode;
    await advance(800);                 // status stops arriving
    const before = hbCount();
    await advance(2000);
    report[mode] = { hbAfterStale: hbCount() - before, view: snapshot() };
    statusMode = "ok";
    await advance(2000);                // recover between scenarios
  }
  // ---- joint model -----------------------------------------------------------
  const T = context.window.RobotTwin;
  report.model = { lines: draw2.mdCanvas, joints: el("joints").innerHTML };
  report.fk = {
    geo: T.geo,
    neutral: ["LF", "RF", "LH", "RH"].map((leg) => T.fkLeg(leg, 90, 45, 90)),
    straight: T.fkLeg("LF", 90, 45, 180),
    betaUp: T.fkLeg("RF", 90, 60, 90),
    alphaUp: { LF: T.fkLeg("LF", 100, 45, 90), RF: T.fkLeg("RF", 100, 45, 90) },
    gammaSweep: [60, 90, 120, 150].map((g) => T.fkLeg("LH", 90, 45, g)),
    withCorr: T.modelAngles(okStatus(), true), noCorr: T.modelAngles(okStatus(), false),
    missing: T.modelAngles({ angles: { LF: [90, 45, null] } }, true),
  };
  report.model.trimNoteOld = el("mdTrim").textContent;          // firmware without standTrimDeg
  sim.trim = { LF: 9, RF: 1, LH: -1, RH: -9 };
  await advance(600);
  report.model.trimNoteNew = el("mdTrim").textContent;
  report.model.jointsWithTrim = el("joints").innerHTML;
  report.fk.withTrim = T.modelAngles(okStatus(), true);
  report.fk.trimNoCorr = T.modelAngles(okStatus(), false);
  report.fk.badTrim = T.modelAngles(Object.assign(okStatus(), { standTrimDeg: { LF: 9, RF: 1, LH: null, RH: -9 } }), true);
  report.model.levelNoteKnown = el("mdTrim").textContent;
  report.fk.noLevel = T.modelAngles(Object.assign(okStatus(), { level: { enabled: true } }), true);
  report.fk.badLevel = T.modelAngles(Object.assign(okStatus(), { level: { gammaOffsetDeg: { LF: "x", RF: 1, LH: -1, RH: 2 } } }), true);
  sim.noOffsets = true;
  await advance(600);
  report.model.levelNoteMissing = el("mdTrim").textContent;
  sim.noOffsets = false;
  await advance(600);
  el("mdCorr").onclick();                 // corrections off
  await advance(100);
  report.model.trimNoteOff = el("mdTrim").textContent;
  el("mdCorr").onclick();                 // back on
  sim.trim = null;
  await advance(600);
  sim.gamma = 170;                       // above the firmware limit: still drawn, no NaN
  await advance(600);
  report.model.outOfRange = el("joints").innerHTML;
  sim.gamma = 90;

  // ---- optional 3D renderer (three.js module is not run here: a fake renderer) --
  const gl3 = { report: { noteBefore: el("mdGlNote").textContent, btnBefore: el("mdGl").disabled } };
  const fakeGl = { draws: [], shown: [], fail: false,
    show(on) { this.shown.push(on); },
    draw(o) { if (this.fail) throw new Error("gl failure"); this.draws.push({ ortho: o.ortho, legs: Object.keys(o.angles).filter((k) => o.angles[k]).length });
      return (p) => [p[0], p[1]]; } };
  T.attach3d(fakeGl);
  let lines0 = draw2.mdCanvas;
  await advance(600);
  gl3.report.ok = { note: el("mdGlNote").textContent, btn: el("mdGl").disabled, draws: fakeGl.draws.length,
    lastShown: fakeGl.shown[fakeGl.shown.length - 1], lines2d: draw2.mdCanvas - lines0, last: fakeGl.draws[fakeGl.draws.length - 1] };
  el("mdVSide").onclick();
  gl3.report.side = fakeGl.draws[fakeGl.draws.length - 1];
  el("mdV3d").onclick();
  el("mdGl").onclick();                   // 3D off: back to the 2D drawing
  lines0 = draw2.mdCanvas;
  await advance(600);
  gl3.report.off = { lastShown: fakeGl.shown[fakeGl.shown.length - 1], lines2d: draw2.mdCanvas - lines0, note: el("mdGlNote").textContent };
  el("mdGl").onclick();
  await advance(600);
  statusMode = "fail";                    // stale pose: the initial pose is drawn, marked as not live
  await advance(2500);
  gl3.report.stale = { lastShown: fakeGl.shown[fakeGl.shown.length - 1], last: fakeGl.draws[fakeGl.draws.length - 1],
    bannerHidden: el("mdBanner").hidden, banner: el("mdBanner").textContent, joints: el("joints").innerHTML };
  statusMode = "ok";
  await advance(600);
  fakeGl.fail = true;                     // renderer breaks: fall back to 2D for good
  lines0 = draw2.mdCanvas;
  await advance(600);
  gl3.report.broken = { lastShown: fakeGl.shown[fakeGl.shown.length - 1], lines2d: draw2.mdCanvas - lines0,
    note: el("mdGlNote").textContent, btn: el("mdGl").disabled, joints: el("joints").innerHTML };
  T.fail3d("test");
  report.gl3 = gl3.report;

  // ---- leveling ON/OFF comparison ---------------------------------------------
  const cmpView = () => ({ summary: el("cmpSummary").textContent, body: el("cmpBody").innerHTML, state: el("cmpState").textContent });
  const postsBefore = posts.length - hbCount();
  el("cmpClear").onclick();
  sim.armed = false;
  await advance(600);
  report.cmpEmpty = cmpView();           // disarmed: nothing is recorded
  sim.armed = true;
  sim.levelEnabled = false; sim.levelActive = false; sim.roll = 3; sim.pitch = -2;
  await advance(8000);                   // OFF segment
  report.cmpOffOnly = cmpView();
  sim.levelEnabled = true; sim.levelActive = false;
  await advance(8000);                   // enabled but not running: must not count as ON
  report.cmpInactive = cmpView();
  sim.levelActive = true; sim.roll = 1; sim.pitch = -1;
  await advance(8000);                   // ON segment
  report.cmpBoth = cmpView();
  report.cmpLines = { roll: draw2.cmpRoll, pitch: draw2.cmpPitch };
  report.cmpCsvHead = T.cmpCsv().split("\n").slice(0, 2);
  sim.walk = "STOPPED"; sim.levelEnabled = false; sim.levelActive = false; sim.roll = 5;
  await advance(8000);                   // other motion: OFF only, must not pair with the FORWARD ON segment
  report.cmpOtherCond = cmpView();
  imuMapped = false;
  await advance(1000);
  report.cmpUnmapped = cmpView();
  imuMapped = true;
  report.cmpSentCommands = posts.length - hbCount() - postsBefore;   // comparison must never send commands

  // ---- comparison statistics: pure function, settle exclusion, 60 s window -------
  const mixed = [[0, 3, -2], [250, -1, 2], [500, -4, -6], [750, 2, 6]].map(([t, a, b]) => ({ t, a, b }));
  report.cmpDirect = { a: T.cmpStats(mixed, "a"), b: T.cmpStats(mixed, "b"), empty: T.cmpStats([], "a"),
    single: T.cmpStats([{ t: 5000, a: -1.5, b: 0 }], "a") };
  const fwdOffRows = () => T.cmpCsv().split("\n").slice(1).map((l) => l.split(","))
    .filter((c) => c[0] === "FORWARD" && c[1] === "off").map((c) => ({ sec: Number(c[2]), used: Number(c[5]) }));
  sim.walk = "FORWARD"; sim.levelEnabled = false; sim.levelActive = false; sim.roll = 2; sim.pitch = -3;
  await advance(600);                    // let the new motion reach the page before clearing
  el("cmpClear").onclick();
  await advance(3000);                   // short segment: its first second must not count
  report.cmpSettle = { rows: fwdOffRows(), view: cmpView() };
  await advance(70000);                  // same segment kept running past the 60 s window
  report.cmpWindow = { rows: fwdOffRows(), view: cmpView() };

  // ---- a drawing failure must not freeze the status text ------------------------
  const boom = () => { throw new Error("canvas failure"); };
  const savedCtx = ["mdCanvas", "cmpRoll"].map((id) => [id, el(id).getContext]);
  savedCtx.forEach(([id]) => { el(id).getContext = boom; });
  sim.walk = "LEFT";
  await advance(1000);
  report.drawFail = { fresh: snapshot() };
  statusMode = "fail";
  await advance(2500);
  report.drawFail.stale = snapshot();
  report.drawFail.staleTrim = el("mdTrim").textContent;
  statusMode = "ok";
  savedCtx.forEach(([id, fn]) => { el(id).getContext = fn; });
  await advance(600);

  // ---- page served by the robot: USB cannot be reached, only explained ---------
  report.robotPage = { initial: connView() };
  el("cmUsb").onclick();
  await advance(600);
  report.robotPage.usb = connView();
  process.stdout.write(JSON.stringify(report));
})();
