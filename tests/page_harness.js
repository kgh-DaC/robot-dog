// Runs the control page script (robot_control_web/index.html) in a fake
// browser: fake DOM, fake clock/timers and a scripted fetch. Prints a JSON
// report for tests/test_control_page.py.  Usage: node page_harness.js <index.html>
"use strict";
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

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
    gammaOffsetDeg: { LF: -2, RF: 1, LH: -1, RH: 2 }, maxCompDeg: 6, tiltLimitDeg: 20 },
  ...(sim.trim ? { standTrimDeg: sim.trim } : {}),     // older firmware: field absent
});
const response = (obj) => ({ json: () => Promise.resolve(obj), status: 200 });

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
      posts.push({ t: now, body: opts.body });
      return Promise.resolve(response({ ok: true }));
    }
    if (statusMode === "fail") return Promise.reject(new Error("network"));
    if (statusMode === "hang") {
      return new Promise((_, reject) => {
        if (opts && opts.signal) opts.signal.onabort = () => reject(new Error("aborted"));
      });
    }
    return Promise.resolve(response(okStatus()));
  },
};
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

(async () => {
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
  process.stdout.write(JSON.stringify(report));
})();
