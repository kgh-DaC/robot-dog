"""Robot dog main board (50 x 70 mm, 2 layers) generator.

Built from 회로도.jpg (Fritzing breadboard view) and the pin table in
esp32-robot-dog-code-master/tools/servoInitPose/servoInitPose.ino.
ESP32 board: 30-pin DevKit V1 (user confirmed 2026-11-16).

One script holds the netlist, placement and routing, then:
  * checks clearances and that every net is connected (DRC),
  * writes Gerber + Excellon files (zip for a PCB maker),
  * writes a KiCad 8 board file (.kicad_pcb) for further editing,
  * renders top/bottom preview PNGs.

Needs: shapely, matplotlib.   Run:  python make_pcb.py [out_dir]
Coordinates are millimetres, origin top-left, y grows downward (KiCad style).
"""
import math
import os
import sys
import uuid
import zipfile
import datetime

from shapely.geometry import Point, Polygon, LineString, box, MultiPolygon, GeometryCollection
from shapely.ops import unary_union
from shapely import affinity

W, H = 50.0, 70.0
CORNER_R = 2.0
CLR = 0.30          # copper clearance between different nets (design rule)
POUR_CLR = 0.35     # GND pour clearance to other nets
EDGE_CLR = 0.40     # copper to board edge
QS = 16             # circle segments per quarter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
NAME = "robotdog_main"

# ----------------------------------------------------------------- model
class Pad:
    def __init__(self, ref, num, x, y, net, shape="circle", w=1.7, h=None, drill=1.0, npth=False):
        self.ref, self.num, self.x, self.y, self.net = ref, str(num), x, y, net
        self.shape, self.w, self.h, self.drill, self.npth = shape, w, (h if h is not None else w), drill, npth

    def geom(self, grow=0.0):
        if self.npth:
            return Point(self.x, self.y).buffer(self.drill / 2 + grow, quad_segs=QS)
        if self.shape == "circle":
            return Point(self.x, self.y).buffer(self.w / 2 + grow, quad_segs=QS)
        if self.shape == "oval":
            r = min(self.w, self.h) / 2
            dx, dy = self.w / 2 - r, self.h / 2 - r
            return LineString([(self.x - dx, self.y - dy), (self.x + dx, self.y + dy)]).buffer(r + grow, quad_segs=QS)
        g = box(self.x - self.w / 2, self.y - self.h / 2, self.x + self.w / 2, self.y + self.h / 2)
        return g.buffer(grow, join_style="mitre") if grow else g


class Footprint:
    def __init__(self, ref, value, x, y, desc=""):
        self.ref, self.value, self.x, self.y, self.desc = ref, value, x, y, desc
        self.pads = []
        self.silk = []     # ("line", (x1,y1,x2,y2)) | ("circle", (cx,cy,r))  absolute
        self.fab = []

    def pad(self, num, x, y, net, **kw):
        p = Pad(self.ref, num, x, y, net, **kw)
        self.pads.append(p)
        return p

    def rect(self, x1, y1, x2, y2, layer="silk"):
        lst = self.silk if layer == "silk" else self.fab
        for a in [(x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)]:
            lst.append(("line", a))


FPS = []
TRACKS = []   # (layer 'F'|'B', width, [(x,y)...], net)
VIAS = []     # (x, y, net)
TEXTS = []    # (text, x, y, height, rot, layer 'F'|'B', anchor 'c'|'l'|'r')
VIA_D, VIA_DRILL = 0.8, 0.4


def fp(*a, **k):
    f = Footprint(*a, **k)
    FPS.append(f)
    return f


def trk(layer, width, pts, net):
    TRACKS.append((layer, width, pts, net))


def txt(s, x, y, h=1.0, rot=0, layer="F", anchor="c"):
    TEXTS.append((s, x, y, h, rot, layer, anchor))


# ------------------------------------------------------------ placement
P = 2.54
Y0 = 8.0                     # first ESP32 header pin (EN / D23); antenna end overhangs the top edge
XL = 12.3                    # left header row
XR = 37.7                    # right row for 25.4 mm (1.0") wide 30-pin boards (DOIT DevKit V1, ~28 mm wide)
XR2 = XR - P                 # right row for 22.86 mm (0.9") narrow 30-pin clones; fit ONE female header
NPIN = 15


def ry(i):
    return round(Y0 + P * i, 3)


# ESP32 DevKit V1 30-pin, antenna up, seen from the top
ESP_L = ["EN", "VP", "VN", "34", "35", "32", "33", "25", "26", "27", "14", "12", "13", "GND", "VIN"]
ESP_R = ["23", "22", "TX", "RX", "21", "19", "18", "5", "17", "16", "4", "2", "15", "GND", "3V3"]

# (row index on the ESP32 header, joint name, GPIO)  -- servos[] in servoInitPose.ino:
# LF {25,26,27}  RF {16,18,17}  LH {13,12,14}  RH {4,2,15}  (each ALPHA, BETA, GAMMA)
LEFT_SERVOS = [(7, "LF-A", 25), (8, "LF-B", 26), (9, "LF-G", 27), (10, "LH-G", 14), (11, "LH-B", 12), (12, "LH-A", 13)]
RIGHT_SERVOS = [(6, "RF-B", 18), (8, "RF-G", 17), (9, "RF-A", 16), (10, "RH-A", 4), (11, "RH-B", 2), (12, "RH-G", 15)]


def esp_net(side, i):
    name = (ESP_L if side == "L" else ESP_R)[i]
    if name == "GND":
        return "GND"
    if name == "3V3":
        return "+3V3"
    if name == "VIN":
        return "+5V_ESP"
    if name == "21":
        return "SDA"
    if name == "22":
        return "SCL"
    for (ri, j, g) in (LEFT_SERVOS if side == "L" else RIGHT_SERVOS):
        if ri == i:
            return f"SRV_{j.replace('-', '_')}_IO{g}"
    return None   # unused pin (no net)


# U1: ESP32 DevKit V1 30-pin on 1x15 female headers (right row has two hole columns)
u1 = fp("U1", "ESP32-DevKit-30P", 25.0, ry(7), "ESP32 DevKit V1 30-pin, 1x15 female headers; right row 25.4 or 22.86 mm")
for i in range(NPIN):
    u1.pad(i + 1, XL, ry(i), esp_net("L", i), shape="rect" if i == 0 else "circle")
    u1.pad(16 + i, XR, ry(i), esp_net("R", i))
    u1.pad(31 + i, XR2, ry(i), esp_net("R", i))
for x in (XL, XR):
    u1.rect(x - 1.27, ry(0) - 1.27, x + 1.27, ry(NPIN - 1) + 1.27)
u1.rect(11.0, ry(0) - 12.4, 39.0, ry(NPIN - 1) + 3.5, layer="fab")     # 28 x 51.5 board outline (raised)

# U2: MPU9250 (GY-9250) 1x10 header, its SCL pin level with the ESP32 SCL pin
MPU_X = 31.5
MY0 = ry(1) - 2 * P
MPU_PINS = ["VCC", "GND", "SCL", "SDA", "EDA", "ECL", "AD0", "INT", "NCS", "FSYNC"]
MPU_NETS = ["+3V3", "GND", "SCL", "SDA", None, None, "GND", None, None, None]   # AD0 -> GND (I2C 0x68)


def my(k):
    return round(MY0 + P * k, 3)


u2 = fp("U2", "MPU9250 (GY-9250)", 25.0, my(4.5), "MPU9250 module 25.5x15.5, 1x10 header")
for k in range(10):
    u2.pad(k + 1, MPU_X, my(k), MPU_NETS[k], shape="rect" if k == 0 else "circle")
u2.rect(MPU_X - 14.2, my(0) - 1.3, MPU_X + 1.3, my(0) - 1.3 + 25.5)

# U3: INA219 module (Adafruit 904 order), 1x6 header, bottom middle (USB plug passes above it)
INA_Y = 51.0
INA_PINS = ["VCC", "GND", "SCL", "SDA", "VIN-", "VIN+"]
INA_NETS = ["+3V3", "GND", "SCL", "SDA", "VIN_BUCK", "VBAT_SW"]
INA_X = [round(25.0 + (k - 2.5) * P, 3) for k in range(6)]
u3 = fp("U3", "INA219 module", 25.0, INA_Y, "INA219 breakout (Adafruit 904 pin order), 1x6 header, screw terminal NOT fitted")
for k in range(6):
    u3.pad(k + 1, INA_X[k], INA_Y, INA_NETS[k], shape="rect" if k == 0 else "circle")
u3.rect(12.2, INA_Y - 1.4, 37.8, INA_Y - 1.4 + 20.4)


def mini360(ref, value, pads, outline, desc):
    """pads: list of (num, name, x, y, net, vertical)"""
    f = fp(ref, value, (outline[0] + outline[2]) / 2, (outline[1] + outline[3]) / 2, desc)
    for (num, name, x, y, net, vertical) in pads:
        w, h = (1.8, 2.4) if vertical else (2.4, 1.8)
        f.pad(num, x, y, net, shape="rect", w=w, h=h, drill=1.0)
    f.rect(*outline)
    return f


# PS1: ESP32 buck (5.2 V), horizontal, under the ESP32 below the MPU.  OUT end left, + side down.
PS1C = (23.6, 36.1)
cx, cy = PS1C
ps1 = mini360("PS1", "Mini360 -> 5.2V (ESP32)", [
    (1, "IN+", cx + 7.2, cy + 4.2, "VIN_BUCK", False), (2, "IN-", cx + 7.2, cy - 4.2, "GND", False),
    (3, "OUT+", cx - 7.2, cy + 4.2, "+5V_ESP", False), (4, "OUT-", cx - 7.2, cy - 4.2, "GND", False)],
    (cx - 8.6, cy - 5.6, cx + 8.6, cy + 5.6), "Mini360 buck 17.2x11.2")
PS1_OUTP = (cx - 7.2, cy + 4.2)
PS1_INP = (cx + 7.2, cy + 4.2)
# PS2: left servo rail (6 V), vertical, OUT end up, + side towards the ESP32
cx, cy = 5.6, 49.5
ps2 = mini360("PS2", "Mini360 -> 6V (servo L)", [
    (1, "IN+", cx + 4.2, cy + 7.2, "VIN_BUCK", True), (2, "IN-", cx - 4.2, cy + 7.2, "GND", True),
    (3, "OUT+", cx + 4.2, cy - 7.2, "+6V_SRV_L", True), (4, "OUT-", cx - 4.2, cy - 7.2, "GND", True)],
    (cx - 5.6, cy - 8.6, cx + 5.6, cy + 8.6), "Mini360 buck 17.2x11.2")
PS2_OUTP, PS2_INP = (cx + 4.2, cy - 7.2), (cx + 4.2, cy + 7.2)
# PS3: right servo rail (6 V), mirrored
cx, cy = 44.4, 49.5
ps3 = mini360("PS3", "Mini360 -> 6V (servo R)", [
    (1, "IN+", cx - 4.2, cy + 7.2, "VIN_BUCK", True), (2, "IN-", cx + 4.2, cy + 7.2, "GND", True),
    (3, "OUT+", cx - 4.2, cy - 7.2, "+6V_SRV_R", True), (4, "OUT-", cx + 4.2, cy - 7.2, "GND", True)],
    (cx - 5.6, cy - 8.6, cx + 5.6, cy + 8.6), "Mini360 buck 17.2x11.2")
PS3_OUTP, PS3_INP = (cx - 4.2, cy - 7.2), (cx - 4.2, cy + 7.2)

# Servo headers 1x3 (pin1 = signal, square). Left: GND outer, V+ middle, S inner.
LS = {"GND": 2.6, "V": 5.14, "S": 7.68}
RS = {"GND": 47.4, "V": 44.86, "S": 42.32}
SERVO_FPS = []
n = 2
for side, lst, xs, rail in (("L", LEFT_SERVOS, LS, "+6V_SRV_L"), ("R", RIGHT_SERVOS, RS, "+6V_SRV_R")):
    for (i, joint, gpio) in lst:
        y = ry(i)
        f = fp(f"J{n}", f"Servo {joint} (IO{gpio})", xs["V"], y, "1x3 pin header 2.54mm, S/+/-")
        f.pad(1, xs["S"], y, esp_net(side, i), shape="rect")
        f.pad(2, xs["V"], y, rail)
        f.pad(3, xs["GND"], y, "GND")
        SERVO_FPS.append((f, side, i, joint, gpio))
        n += 1
LAST_L = ry(max(i for (i, j, g) in LEFT_SERVOS))
LAST_R = ry(max(i for (i, j, g) in RIGHT_SERVOS))

# J1: battery input after the external switch (3.5 mm screw terminal), bottom-right corner
j1 = fp("J1", "BAT (after switch)", 43.25, 66.2, "2-pin screw terminal 3.5mm (KF350-2P)")
j1.pad(1, 41.5, 66.2, "VBAT_SW", shape="rect", w=2.2, drill=1.2)
j1.pad(2, 45.0, 66.2, "GND", shape="circle", w=2.2, drill=1.2)
j1.rect(39.75, 62.6, 46.75, 69.8)

# Capacitors
C1Y = 45.5
c1 = fp("C1", "470uF 10V (6.3mm)", 19.0, C1Y, "radial 6.3mm, pitch 2.5")
c1.pad(1, 17.75, C1Y, "+5V_ESP", shape="rect", w=1.6, drill=0.8)
c1.pad(2, 20.25, C1Y, "GND", w=1.6, drill=0.8)
c1.silk.append(("circle", (19.0, C1Y, 3.25)))
CL_X, CR_X = 5.14, 44.86
c2 = fp("C2", "1000uF 10V (8mm)", CL_X, 15.0, "radial 8mm, pitch 3.5")
c2.pad(1, CL_X, 16.75, "+6V_SRV_L", shape="rect", w=1.8, drill=1.0)
c2.pad(2, CL_X, 13.25, "GND", w=1.8, drill=1.0)
c2.silk.append(("circle", (CL_X, 15.0, 4.1)))
c3 = fp("C3", "1000uF 10V (8mm)", CR_X, 15.0, "radial 8mm, pitch 3.5")
c3.pad(1, CR_X, 16.75, "+6V_SRV_R", shape="rect", w=1.8, drill=1.0)
c3.pad(2, CR_X, 13.25, "GND", w=1.8, drill=1.0)
c3.silk.append(("circle", (CR_X, 15.0, 4.1)))

# Mounting holes M3 (bottom-right corner is used by J1)
MH = [(3.5, 3.5), (46.5, 3.5), (3.5, 66.5)]
for k, (x, y) in enumerate(MH):
    f = fp(f"H{k + 1}", "M3", x, y, "M3 mounting hole, NPTH 3.2")
    f.pad("", x, y, None, drill=3.2, npth=True)
    f.silk.append(("circle", (x, y, 3.0)))

# ----------------------------------------------------------------- routing
# both right hole columns carry the same signal
for i in range(NPIN):
    if esp_net("R", i):
        trk("F", 0.4, [(XR2, ry(i)), (XR, ry(i))], esp_net("R", i))
# servo signals (straight, top)
for (f, side, i, joint, gpio) in SERVO_FPS:
    xs = (LS if side == "L" else RS)["S"]
    trk("F", 0.4, [(xs, ry(i)), (XL if side == "L" else XR, ry(i))], esp_net(side, i))
# servo rails: column on both layers, cap at the top, buck at the bottom
for lay in ("F", "B"):
    trk(lay, 1.5, [(CL_X, 16.75), (CL_X, LAST_L)], "+6V_SRV_L")
    trk(lay, 1.5, [(CR_X, 16.75), (CR_X, LAST_R)], "+6V_SRV_R")
trk("F", 2.0, [(CL_X, LAST_L), (CL_X, LAST_L + 1.5), (PS2_OUTP[0] - 2.36, PS2_OUTP[1]), PS2_OUTP], "+6V_SRV_L")
trk("F", 2.0, [(CR_X, LAST_R), (CR_X, LAST_R + 1.5), (PS3_OUTP[0] + 2.36, PS3_OUTP[1]), PS3_OUTP], "+6V_SRV_R")

# battery (after switch) -> INA219 VIN+   (top)
trk("F", 2.0, [(41.5, 66.2), (37.0, 61.7), (37.0, 53.4), (34.6, 51.0), (INA_X[5], INA_Y)], "VBAT_SW")
# INA219 VIN- -> three bucks   (bottom)
trk("B", 2.0, [(INA_X[4], INA_Y), (INA_X[4], 55.0)], "VIN_BUCK")
trk("B", 2.0, [PS2_INP, (PS2_INP[0] + 1.7, 55.0), (36.0, 55.0), (PS3_INP[0] - 2.5, PS3_INP[1]), PS3_INP], "VIN_BUCK")
trk("B", 2.0, [(INA_X[4], INA_Y), (INA_X[4], 49.5), (PS1_INP[0], 47.5), PS1_INP], "VIN_BUCK")
# 5 V: PS1 OUT+ -> C1+ -> ESP32 VIN pin   (top)
trk("F", 1.0, [PS1_OUTP, (PS1_OUTP[0], 42.2), (17.75, 43.55), (17.75, C1Y)], "+5V_ESP")
trk("F", 1.0, [(PS1_OUTP[0], 42.2), (PS1_OUTP[0] - 1.36, ry(14)), (XL, ry(14))], "+5V_ESP")
# 3V3 (ESP32 right row, bottom pin): up to MPU VCC on the bottom layer, across to INA219 VCC on top
X3 = 33.33                    # channel between the MPU column and the narrow right row
trk("B", 0.5, [(XR2, ry(14)), (X3, ry(14) - 1.83), (X3, my(0)), (MPU_X, my(0))], "+3V3")
trk("F", 0.5, [(XR2, ry(14)), (33.0, 45.72), (22.5, 45.72), (22.5, 48.0), (20.0, 48.0), (INA_X[0], 49.35), (INA_X[0], INA_Y)], "+3V3")
# I2C: ESP32 -> MPU on top, MPU -> INA219 on the bottom
trk("F", 0.3, [(XR2, ry(1)), (MPU_X, my(2))], "SCL")
trk("F", 0.3, [(XR2, ry(4)), (X3, ry(4)), (X3, my(3) + 1.12), (MPU_X + 0.7, my(3)), (MPU_X, my(3))], "SDA")
trk("B", 0.3, [(MPU_X, my(2)), (27.0, my(2)), (27.0, 47.0), (INA_X[2], 50.27), (INA_X[2], INA_Y)], "SCL")
trk("B", 0.3, [(MPU_X, my(3)), (28.0, my(3)), (28.0, 47.5), (INA_X[3], 49.23), (INA_X[3], INA_Y)], "SDA")
# MPU9250 AD0 -> GND: explicit trace up to the module's own GND pin => address 0x68
trk("F", 0.4, [(MPU_X, my(6)), (29.9, my(6)), (29.9, my(1)), (MPU_X, my(1))], "GND")
# ESP32 GND pins tied together on top as well (pour also joins them)
trk("F", 0.6, [(XL, ry(13)), (XL + 1.9, ry(13))], "GND")

# ------------------------------------------------------------------ silk
# per header: joint letter + GPIO, e.g. "A25" (A = ALPHA hip, B = BETA shoulder, G = GAMMA knee)
for (f, side, i, joint, gpio) in SERVO_FPS:
    label = joint.split("-")[1] + str(gpio)
    txt(label, 9.8 if side == "L" else 40.2, ry(i), 0.8)
# a short line on the outer edge where one leg's group ends and the next begins
for side, lst, x1, x2 in (("L", LEFT_SERVOS, 0.25, 1.75), ("R", RIGHT_SERVOS, 48.25, 49.75)):
    for (a, b) in zip(lst, lst[1:]):
        if a[1][:2] != b[1][:2]:
            yb = (ry(a[0]) + ry(b[0])) / 2
            fpj = next(f for (f, s, i, j, g) in SERVO_FPS if s == side and i == b[0])
            fpj.silk.append(("line", (x1, yb, x2, yb)))
for k, s in enumerate(("A:ALPHA", "B:BETA", "G:GAMMA")):
    txt(s, 5.6, 59.3 + k * 1.3, 0.8)
SY = min(ry(LEFT_SERVOS[0][0]), ry(RIGHT_SERVOS[0][0])) - 1.85
for x, s in ((LS["GND"], "-"), (LS["V"], "+"), (LS["S"], "S"), (RS["GND"], "-"), (RS["V"], "+"), (RS["S"], "S")):
    txt(s, x, SY, 1.0)
txt("LF", 0.95, ry(8), 1.0, rot=90)
txt("LH", 0.95, ry(11), 1.0, rot=90)
txt("RF", 49.05, ry(7.5), 1.0, rot=90)
txt("RH", 49.05, ry(11), 1.0, rot=90)
for k, name in enumerate(MPU_PINS):
    txt(name[:3] if name != "FSYNC" else "FSY", 29.4, my(k), 0.9, anchor="r")
txt("AD0=GND", 23.0, my(7.4), 1.0)
txt("MPU9250", 21.3, my(5.0), 1.1)
for k, name in enumerate(INA_PINS):
    txt(name, INA_X[k], INA_Y + 1.6, 0.9, rot=90, anchor="r")
txt("INA219", 25.0, 61.0, 1.4)
txt("(no screw terminal)", 25.0, 63.2, 1.0)
# buck labels
txt("PS1 5.2V", PS1C[0], PS1C[1] - 1.0, 1.1)
txt("IN", PS1C[0] + 4.6, PS1C[1] + 2.3, 0.9)
txt("OUT", PS1C[0] - 4.4, PS1C[1] + 2.3, 0.9)
txt("+", PS1_OUTP[0], PS1_OUTP[1] - 2.0, 0.9)
txt("+", PS1_INP[0], PS1_INP[1] - 2.0, 0.9)
txt("PS2 6V", 5.6, 49.5, 1.0, rot=90)
txt("OUT", 5.6, 43.9, 0.9)
txt("IN", 5.6, 55.1, 0.9)
txt("+", PS2_INP[0], PS2_INP[1] + 1.95, 0.9)
txt("PS3 6V", 44.4, 49.5, 1.0, rot=90)
txt("OUT", 44.4, 43.9, 0.9)
txt("IN", 44.4, 55.1, 0.9)
txt("+", PS3_INP[0], PS3_INP[1] + 1.95, 0.9)
txt("+", 41.5, 63.7, 1.0)
txt("-", 45.0, 63.7, 1.0)
txt("BAT", 43.25, 68.6, 1.0)
txt("+", 16.4, C1Y + 2.6, 0.9)
txt("+", 6.9, 18.4, 0.9)
txt("+", 46.6, 18.4, 0.9)
txt("C2", CL_X, 9.9, 1.0)
txt("C3", CR_X, 9.9, 1.0)
txt("EN", 14.2, ry(0), 0.8, anchor="l")
txt("VIN", 14.2, ry(14), 0.8, anchor="l")
txt("3V3", 33.0, ry(14) + 1.5, 0.8, anchor="r")
txt("25.4", XR, ry(0) - 3.4, 0.8, rot=90)
txt("22.9", XR2, ry(0) - 3.4, 0.8, rot=90)
txt("ESP32 30P", 21.0, 3.2, 0.9)
txt("USB v", 30.0, 48.9, 0.8)
# bottom silk: title + servo map
txt("ROBOT DOG MAIN v2", 20.5, 4.0, 1.1, layer="B")
txt("2026-11-16  50x70", 20.5, 6.0, 0.9, layer="B")
rows = ["SERVO   GPIO", "LF A/B/G 25/26/27", "RF A/B/G 16/18/17", "LH A/B/G 13/12/14", "RH A/B/G 4/2/15",
        "IMU AD0=GND 0x68", "INA219 0x40", "I2C SDA21 SCL22"]
for k, r in enumerate(rows):
    txt(r, 20.5, 9.5 + k * 1.75, 0.9, layer="B")
txt("BAT+ > SW > J1+", 20.5, 26.6, 0.9, layer="B")
txt("ESP32 30P: header in", 20.5, 28.6, 0.9, layer="B")
txt("25.4 OR 22.9 row", 20.5, 30.3, 0.9, layer="B")


# =================================================================== geometry
def board_outline(inset=0.0):
    r = CORNER_R
    return box(r, r, W - r, H - r).buffer(r - inset, quad_segs=QS) if inset < r else box(inset, inset, W - inset, H - inset)


BOARD = board_outline()
ALL_PADS = [p for f in FPS for p in f.pads]
CU_PADS = [p for p in ALL_PADS if not p.npth]
NPTH = [p for p in ALL_PADS if p.npth]


def track_geom(t):
    layer, w, pts, net = t
    if len(pts) == 1:
        return Point(pts[0]).buffer(w / 2, quad_segs=QS)
    return LineString(pts).buffer(w / 2, quad_segs=QS)


def via_geom(v):
    return Point(v[0], v[1]).buffer(VIA_D / 2, quad_segs=QS)


def layer_items(layer):
    """(net, geom, kind) for every copper item on a layer, pours excluded"""
    out = [(p.net, p.geom(), "pad", p) for p in CU_PADS]
    out += [(t[3], track_geom(t), "track", t) for t in TRACKS if t[0] == layer]
    out += [(v[2], via_geom(v), "via", v) for v in VIAS]
    return out


def thermal_pour(layer, extra_vias=()):
    items = layer_items(layer)
    other = unary_union([g.buffer(POUR_CLR, quad_segs=QS) for (net, g, k, o) in items if net != "GND"])
    keep = unary_union([Point(x, y).buffer(3.0, quad_segs=QS) for (x, y) in MH])
    antenna = box(13.6, 0.0, 36.4, 2.5)
    base = board_outline(0.5).difference(other).difference(keep).difference(antenna)
    base = base.buffer(-0.15, quad_segs=QS).buffer(0.15, quad_segs=QS).intersection(board_outline(0.5)).difference(other)
    rings, spokes = [], []
    for p in CU_PADS:
        if p.net != "GND":
            continue
        ring = p.geom(0.3)
        rings.append(ring)
        L = max(p.w, p.h) + 1.5
        bars = unary_union([box(p.x - L, p.y - 0.3, p.x + L, p.y + 0.3), box(p.x - 0.3, p.y - L, p.x + 0.3, p.y + L)])
        spokes.append(bars.intersection(p.geom(0.32)))
    pour = base.difference(unary_union(rings))
    pour = unary_union([pour, unary_union(spokes).intersection(base)])
    gnd_items = unary_union([g for (net, g, k, o) in items if net == "GND"] +
                            [Point(x, y).buffer(VIA_D / 2) for (x, y) in extra_vias])
    pieces = list(pour.geoms) if hasattr(pour, "geoms") else [pour]
    pieces = [pc for pc in pieces if pc.area > 0.5 and pc.intersects(gnd_items)]
    return unary_union(pieces)


# first pass pours, then stitching vias where both pours exist
POUR = {"F": thermal_pour("F"), "B": thermal_pour("B")}
both = POUR["F"].intersection(POUR["B"]).buffer(-0.9, quad_segs=QS)
pad_keep = unary_union([p.geom(1.0) for p in ALL_PADS] + [via_geom(v).buffer(1.0) for v in VIAS])
stitch = []
yy = 3.0
while yy < H:
    xx = 2.0 + (1.5 if int(yy / 4.5) % 2 else 0)
    while xx < W:
        pt = Point(xx, yy)
        if both.contains(pt) and not pad_keep.contains(pt) and all(math.hypot(xx - a, yy - b) > 3.5 for (a, b) in stitch):
            stitch.append((xx, yy))
        xx += 4.0
    yy += 4.5
for (x, y) in stitch:
    VIAS.append((round(x, 3), round(y, 3), "GND"))
POUR = {"F": thermal_pour("F"), "B": thermal_pour("B")}


def copper_by_net(layer):
    d = {}
    for (net, g, k, o) in layer_items(layer):
        d.setdefault(net, []).append(g)
    d.setdefault("GND", []).append(POUR[layer])
    return {n: unary_union(gs) for n, gs in d.items()}


# =================================================================== DRC
def drc():
    errors = []
    for layer in ("F", "B"):
        cbn = copper_by_net(layer)
        nets = list(cbn)
        for a in range(len(nets)):
            for b in range(a + 1, len(nets)):
                na, nb = nets[a], nets[b]
                if na is None and nb is None:
                    continue
                dd = cbn[na].distance(cbn[nb])
                if dd < CLR - 1e-3:
                    pa = cbn[na].intersection(cbn[nb].buffer(CLR))
                    c = pa.centroid if not pa.is_empty else None
                    errors.append(f"{layer}.Cu clearance {dd:.3f} mm between {na} and {nb}" + (f" near ({c.x:.2f},{c.y:.2f})" if c else ""))
        allcu = unary_union(list(cbn.values()))
        ed = allcu.distance(BOARD.exterior)
        if not BOARD.buffer(-EDGE_CLR + 1e-3).contains(allcu):
            errors.append(f"{layer}.Cu copper closer than {EDGE_CLR} mm to the board edge (min {ed:.3f})")
        for h in NPTH:
            if allcu.distance(Point(h.x, h.y)) < h.drill / 2 + 0.3:
                errors.append(f"{layer}.Cu copper too close to mounting hole at ({h.x},{h.y})")
    # drill to drill
    holes = [(p.x, p.y, p.drill) for p in ALL_PADS] + [(v[0], v[1], VIA_DRILL) for v in VIAS]
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            a, b = holes[i], holes[j]
            if math.hypot(a[0] - b[0], a[1] - b[1]) - (a[2] + b[2]) / 2 < 0.5:
                errors.append(f"holes too close: {a} {b}")
    # connectivity per net
    layers = {L: copper_by_net(L) for L in ("F", "B")}
    nets = sorted({p.net for p in CU_PADS if p.net})
    report = {}
    for net in nets:
        nodes = []
        for L in ("F", "B"):
            g = layers[L].get(net)
            if g is None:
                continue
            for pc in (g.geoms if hasattr(g, "geoms") else [g]):
                nodes.append((L, pc))
        parent = list(range(len(nodes)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        links = [(p.x, p.y) for p in CU_PADS if p.net == net] + [(v[0], v[1]) for v in VIAS if v[2] == net]
        for (x, y) in links:
            pt = Point(x, y)
            ids = [k for k, (L, pc) in enumerate(nodes) if pc.distance(pt) < 1e-6]
            for k in ids[1:]:
                parent[find(k)] = find(ids[0])
        groups = set()
        for p in CU_PADS:
            if p.net != net:
                continue
            pt = Point(p.x, p.y)
            ids = [k for k, (L, pc) in enumerate(nodes) if pc.distance(pt) < 1e-6]
            groups.add(find(ids[0]) if ids else ("missing", p.ref, p.num))
        report[net] = len(groups)
        if len(groups) != 1:
            pads = [f"{p.ref}.{p.num}" for p in CU_PADS if p.net == net]
            errors.append(f"net {net} is split into {len(groups)} parts ({', '.join(pads)})")
    return errors, report


# =================================================================== text geometry
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
FONT = FontProperties(family="DejaVu Sans", weight="bold")


def text_geom(t):
    s, x, y, h, rot, layer, anchor = t
    tp = TextPath((0, 0), s, size=h / 0.73, prop=FONT)
    g = Polygon()
    for ring in tp.to_polygons(closed_only=True):
        if len(ring) >= 3:
            g = g.symmetric_difference(Polygon(ring).buffer(0))
    if g.is_empty:
        return g
    minx, miny, maxx, maxy = tp.get_extents().bounds[0], 0, tp.get_extents().bounds[0] + tp.get_extents().bounds[2], 0
    dx = {"c": -(minx + maxx) / 2, "l": -minx, "r": -maxx}[anchor]
    g = affinity.translate(g, dx, -h / 2)
    if rot:
        g = affinity.rotate(g, rot, origin=(0, 0))
    if layer == "B":
        g = affinity.scale(g, -1, 1, origin=(0, 0))
    g = affinity.scale(g, 1, -1, origin=(0, 0))       # view (y up) -> model (y down)
    return affinity.translate(g, x, y)


def silk_layer(layer):
    geo = []
    if layer == "F":
        for f in FPS:
            for kind, a in f.silk:
                if kind == "line":
                    geo.append(LineString([(a[0], a[1]), (a[2], a[3])]).buffer(0.075, quad_segs=4))
                else:
                    geo.append(Point(a[0], a[1]).buffer(a[2] + 0.075, quad_segs=QS).exterior.buffer(0.075, quad_segs=4))
    for t in TEXTS:
        if t[5] == layer:
            geo.append(text_geom(t))
    g = unary_union(geo).intersection(board_outline(0.15))
    return g.difference(mask_layer())


def mask_layer():
    return unary_union([p.geom(0.05) for p in ALL_PADS])


def copper_layer(layer):
    items = [g for (net, g, k, o) in layer_items(layer)]
    return unary_union(items + [POUR[layer]])


# =================================================================== gerber
def _c(v):
    return int(round(v * 1e6))


def polys_of(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    out = []
    for sub in g.geoms:
        out += polys_of(sub)
    return out


def gerber(path, func, geom=None, outline=False):
    lines = ["%TF.GenerationSoftware,robotdog,make_pcb.py,1*%", f"%TF.FileFunction,{func}*%",
             "%FSLAX46Y46*%", "%MOMM*%", "%ADD10C,0.100000*%", "%LPD*%", "D10*"]

    def pt(x, y, d):
        return f"X{_c(x)}Y{_c(H - y)}{d}*"
    if outline:
        ring = list(BOARD.exterior.coords)
        lines.append(pt(*ring[0], "D02"))
        lines += [pt(x, y, "D01") for (x, y) in ring[1:]]
    else:
        ps = sorted(polys_of(geom), key=lambda p: -Polygon(p.exterior).area)
        for p in ps:
            for k, ring in enumerate([p.exterior] + list(p.interiors)):
                lines.append("%LPD*%" if k == 0 else "%LPC*%")
                cs = list(ring.coords)
                lines.append("G36*")
                lines.append(pt(*cs[0], "D02"))
                lines += [pt(x, y, "D01") for (x, y) in cs[1:]]
                lines.append("G37*")
            lines.append("%LPD*%")
    lines.append("M02*")
    with open(path, "w", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def excellon(path, holes, plated):
    tools = sorted({round(d, 2) for (x, y, d) in holes})
    L = ["M48", "; robotdog make_pcb.py", "; FORMAT={-:-/ absolute / metric / decimal}",
         f"; #@! TF.FileFunction,{'Plated,1,2,PTH' if plated else 'NonPlated,1,2,NPTH'}", "FMAT,2", "METRIC"]
    for k, d in enumerate(tools):
        L.append(f"T{k + 1}C{d:.3f}")
    L += ["%", "G90", "G05"]
    for k, d in enumerate(tools):
        L.append(f"T{k + 1}")
        for (x, y, dd) in holes:
            if round(dd, 2) == d:
                L.append(f"X{x:.3f}Y{H - y:.3f}")
    L += ["T0", "M30"]
    with open(path, "w", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


# =================================================================== KiCad
def U():
    return str(uuid.uuid4())


def kicad(path):
    nets = [""] + sorted({p.net for p in CU_PADS if p.net} | {t[3] for t in TRACKS} | {v[2] for v in VIAS})
    nid = {n: i for i, n in enumerate(nets)}
    o = ['(kicad_pcb (version 20240108) (generator "pcbnew") (generator_version "8.0")',
         '  (general (thickness 1.6) (legacy_teardrops no))', '  (paper "A4")',
         '  (title_block (title "Robot dog main board") (date "2026-11-16") (rev "1"))', '  (layers']
    for l in ['(0 "F.Cu" signal)', '(31 "B.Cu" signal)', '(32 "B.Adhes" user "B.Adhesive")', '(33 "F.Adhes" user "F.Adhesive")',
              '(34 "B.Paste" user)', '(35 "F.Paste" user)', '(36 "B.SilkS" user "B.Silkscreen")', '(37 "F.SilkS" user "F.Silkscreen")',
              '(38 "B.Mask" user)', '(39 "F.Mask" user)', '(40 "Dwgs.User" user "User.Drawings")', '(41 "Cmts.User" user "User.Comments")',
              '(42 "Eco1.User" user "User.Eco1")', '(43 "Eco2.User" user "User.Eco2")', '(44 "Edge.Cuts" user)', '(45 "Margin" user)',
              '(46 "B.CrtYd" user "B.Courtyard")', '(47 "F.CrtYd" user "F.Courtyard")', '(48 "B.Fab" user)', '(49 "F.Fab" user)']:
        o.append("    " + l)
    o.append("  )")
    o.append("  (setup (pad_to_mask_clearance 0.05) (allow_soldermask_bridges_in_footprints no))")
    for n in nets:
        o.append(f'  (net {nid[n]} "{n}")')

    def eff(h, mirror=False, justify=None):
        j = []
        if justify:
            j.append(justify)
        if mirror:
            j.append("mirror")
        js = f" (justify {' '.join(j)})" if j else ""
        return f'(effects (font (size {h:.2f} {h:.2f}) (thickness {max(0.12, h * 0.15):.2f})){js})'

    for f in FPS:
        o.append(f'  (footprint "RobotDog:{f.ref}_{f.value.split()[0]}" (layer "F.Cu") (uuid "{U()}") (at {f.x:.3f} {f.y:.3f})')
        o.append(f'    (property "Reference" "{f.ref}" (at 0 0 0) (layer "F.Fab") (uuid "{U()}") {eff(0.8)})')
        o.append(f'    (property "Value" "{f.value}" (at 0 1 0) (layer "F.Fab") (hide yes) (uuid "{U()}") {eff(0.8)})')
        o.append(f'    (property "Description" "{f.desc}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{U()}") {eff(0.8)})')
        o.append('    (attr through_hole)')
        for lst, layer in ((f.silk, "F.SilkS"), (f.fab, "F.Fab")):
            for kind, a in lst:
                if kind == "line":
                    o.append(f'    (fp_line (start {a[0] - f.x:.3f} {a[1] - f.y:.3f}) (end {a[2] - f.x:.3f} {a[3] - f.y:.3f}) (stroke (width 0.15) (type solid)) (layer "{layer}") (uuid "{U()}"))')
                else:
                    o.append(f'    (fp_circle (center {a[0] - f.x:.3f} {a[1] - f.y:.3f}) (end {a[0] - f.x + a[2]:.3f} {a[1] - f.y:.3f}) (stroke (width 0.15) (type solid)) (fill none) (layer "{layer}") (uuid "{U()}"))')
        for p in f.pads:
            rel = f"(at {p.x - f.x:.3f} {p.y - f.y:.3f})"
            if p.npth:
                o.append(f'    (pad "" np_thru_hole circle {rel} (size {p.drill} {p.drill}) (drill {p.drill}) (layers "*.Cu" "*.Mask") (uuid "{U()}"))')
                continue
            shape = {"circle": "circle", "rect": "rect", "oval": "oval"}[p.shape]
            net = f' (net {nid[p.net]} "{p.net}")' if p.net else ""
            o.append(f'    (pad "{p.num}" thru_hole {shape} {rel} (size {p.w} {p.h}) (drill {p.drill}) (layers "*.Cu" "*.Mask") (remove_unused_layers no){net} (uuid "{U()}"))')
        o.append("  )")
    # edge cuts
    r = CORNER_R
    segs = [((r, 0), (W - r, 0)), ((W, r), (W, H - r)), ((W - r, H), (r, H)), ((0, H - r), (0, r))]
    for (a, b) in segs:
        o.append(f'  (gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "{U()}"))')
    k = r * (1 - 1 / math.sqrt(2))
    arcs = [((0, r), (k, k), (r, 0)), ((W - r, 0), (W - k, k), (W, r)), ((W, H - r), (W - k, H - k), (W - r, H)), ((r, H), (k, H - k), (0, H - r))]
    for (a, m, b) in arcs:
        o.append(f'  (gr_arc (start {a[0]} {a[1]}) (mid {m[0]:.4f} {m[1]:.4f}) (end {b[0]} {b[1]}) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "{U()}"))')
    for (s, x, y, h, rot, layer, anchor) in TEXTS:
        lay = "F.SilkS" if layer == "F" else "B.SilkS"
        just = {"c": None, "l": "left", "r": "right"}[anchor]
        if layer == "B" and just:
            just = "right" if just == "left" else "left"
        o.append(f'  (gr_text "{s}" (at {x:.3f} {y:.3f} {rot}) (layer "{lay}") (uuid "{U()}") {eff(h, layer == "B", just)})')
    for (layer, w, pts, net) in TRACKS:
        for a, b in zip(pts, pts[1:]):
            o.append(f'  (segment (start {a[0]:.3f} {a[1]:.3f}) (end {b[0]:.3f} {b[1]:.3f}) (width {w}) (layer "{layer}.Cu") (net {nid[net]}) (uuid "{U()}"))')
    for (x, y, net) in VIAS:
        o.append(f'  (via (at {x:.3f} {y:.3f}) (size {VIA_D}) (drill {VIA_DRILL}) (layers "F.Cu" "B.Cu") (net {nid[net]}) (uuid "{U()}"))')
    for layer in ("F", "B"):
        pts = " ".join(f"(xy {x} {y})" for (x, y) in [(0.5, 0.5), (W - 0.5, 0.5), (W - 0.5, H - 0.5), (0.5, H - 0.5)])
        o.append(f'  (zone (net {nid["GND"]}) (net_name "GND") (layer "{layer}.Cu") (uuid "{U()}") (hatch edge 0.5)'
                 f' (connect_pads (clearance {POUR_CLR})) (min_thickness 0.25) (filled_areas_thickness no)'
                 f' (fill yes (thermal_gap 0.3) (thermal_bridge_width 0.6) (island_removal_mode 1) (island_area_min 0.5))'
                 f' (polygon (pts {pts})))')
    o.append(")")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(o) + "\n")


# =================================================================== preview
def render(path, side):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath

    def draw(ax, g, **kw):
        for p in polys_of(g):
            verts, codes = [], []
            for ring in [p.exterior] + list(p.interiors):
                cs = list(ring.coords)
                verts += cs
                codes += [MPath.MOVETO] + [MPath.LINETO] * (len(cs) - 2) + [MPath.CLOSEPOLY]
            ax.add_patch(PathPatch(MPath(verts, codes), **kw))

    fig, ax = plt.subplots(figsize=(6.0, 8.2), dpi=200)
    draw(ax, BOARD, facecolor="#0b3d2a", edgecolor="#d4c25a", lw=0.8)
    top, bot = copper_layer("F"), copper_layer("B")
    if side == "top":
        draw(ax, bot, facecolor="#3a6fd8", edgecolor="none", alpha=0.35)
        draw(ax, top, facecolor="#d9893b", edgecolor="none", alpha=0.85)
        draw(ax, silk_layer("F"), facecolor="#f2f2f2", edgecolor="none")
    else:
        draw(ax, top, facecolor="#d9893b", edgecolor="none", alpha=0.25)
        draw(ax, bot, facecolor="#3a6fd8", edgecolor="none", alpha=0.85)
        draw(ax, silk_layer("B"), facecolor="#f2f2f2", edgecolor="none")
    holes = unary_union([Point(p.x, p.y).buffer(p.drill / 2) for p in ALL_PADS] + [Point(v[0], v[1]).buffer(VIA_DRILL / 2) for v in VIAS])
    draw(ax, holes, facecolor="#111111", edgecolor="none")
    if side == "top":
        for f in FPS:
            for kind, a in f.fab:
                ax.plot([a[0], a[2]], [a[1], a[3]], color="#7fd1ff", lw=0.7, ls="--")
    ax.set_xlim(-1, W + 1)
    ax.set_ylim(H + 1, -1)
    if side == "bottom":
        ax.set_xlim(W + 1, -1)       # seen from below
    ax.set_aspect("equal")
    ax.set_title(f"{NAME}  {side} view  ({W:.0f} x {H:.0f} mm)", fontsize=8)
    ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# =================================================================== main
def main():
    os.makedirs(OUT, exist_ok=True)
    errors, report = drc()
    print(f"footprints {len(FPS)}, pads {len(CU_PADS)}, tracks {len(TRACKS)}, vias {len(VIAS)} ({len(stitch)} GND stitching)")
    for net, k in sorted(report.items()):
        print(f"  net {net:22s} parts={k}")
    if errors:
        print("DRC ERRORS:")
        for e in errors:
            print("  " + e)
    else:
        print("DRC: no clearance / connectivity errors")
    gdir = os.path.join(OUT, "gerber")
    os.makedirs(gdir, exist_ok=True)
    files = {
        f"{NAME}-F_Cu.gtl": ("Copper,L1,Top", copper_layer("F")),
        f"{NAME}-B_Cu.gbl": ("Copper,L2,Bot", copper_layer("B")),
        f"{NAME}-F_Mask.gts": ("Soldermask,Top", mask_layer()),
        f"{NAME}-B_Mask.gbs": ("Soldermask,Bot", mask_layer()),
        f"{NAME}-F_Silkscreen.gto": ("Legend,Top", silk_layer("F")),
        f"{NAME}-B_Silkscreen.gbo": ("Legend,Bot", silk_layer("B")),
    }
    for fn, (func, g) in files.items():
        gerber(os.path.join(gdir, fn), func, g)
    gerber(os.path.join(gdir, f"{NAME}-Edge_Cuts.gm1"), "Profile,NP", outline=True)
    excellon(os.path.join(gdir, f"{NAME}-PTH.drl"), [(p.x, p.y, p.drill) for p in CU_PADS] + [(v[0], v[1], VIA_DRILL) for v in VIAS], True)
    excellon(os.path.join(gdir, f"{NAME}-NPTH.drl"), [(p.x, p.y, p.drill) for p in NPTH], False)
    zpath = os.path.join(OUT, f"{NAME}_gerber.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, fn), fn)
    kicad(os.path.join(OUT, f"{NAME}.kicad_pcb"))
    render(os.path.join(OUT, f"{NAME}_top.png"), "top")
    render(os.path.join(OUT, f"{NAME}_bottom.png"), "bottom")
    print("written:", OUT)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
