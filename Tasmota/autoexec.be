# Yolo-Uno (ESP32-S3) Tasmota gateway -> ThingsBoard (codehelpers.tech) via broker.emqx.io
#
#   - InnoLab BLE light sensor (inno-013-7985) via legacy MI32 Berry BLE
#   - DHT20 (AHT20, I2C 0x38) via Berry I2C (or Tasmota AHT2x driver if built in)
#
# Readings are added to Tasmota's standard SENSOR message, so Tasmota itself publishes them to
#   <FullTopic>  tbch_8x4k/tele/<Topic>/SENSOR   (e.g. tbch_8x4k/tele/sensor1/SENSOR)
# every TelePeriod, e.g.
#   {"Time":"...","InnoLux":{"Illuminance":145.2},"DHT20":{"Temperature":28.4,"Humidity":71.3}}
# The ThingsBoard converter maps these to illuminance / temperature / humidity
# (plus InnoLux_Illuminance, DHT20_Temperature, DHT20_Humidity).
#
# MQTT settings (Configuration -> Configure MQTT): Host broker.emqx.io, Port 1883,
# User/Password empty, Topic sensor1, Full Topic tbch_8x4k/%prefix%/%topic%/
#
# Firmware: env tasmota32s3-mi32 (needed for BLE). SetOption115, I2C pins and TelePeriod are
# set automatically.

import math
import string
import cb
import global
import json

# ---------------- settings ----------------
var TELE_PERIOD_S = 10         # how often Tasmota publishes SENSOR (min 10)
var DHT_PERIOD_S  = 10         # how often the DHT20 is read
var SDA_GPIO = 11              # Yolo-Uno I2C pins (check your board's labels)
var SCL_GPIO = 12

# Globals declared up front (Berry strict mode); callback pointers created ONCE
innolux = nil
dht = nil
dht_ok = false
sensors = {"lux": nil, "lux_ms": 0, "t": nil, "h": nil, "th_ms": 0}
inno_adv_ptr  = cb.gen_cb(/svc, manu -> (global.innolux != nil ? global.innolux.on_adv(svc, manu) : nil))
inno_conn_ptr = cb.gen_cb(/err, op, uuid, handle -> (global.innolux != nil ? global.innolux.on_conn(err, op, uuid, handle) : nil))

# =====================================================================
#  InnoLab BLE light sensor
# =====================================================================
class InnoLuxBLE
  var ble                        # BLE module, imported once the stack is up
  var target_name, svc_uuid, tx_uuid
  var adv_buf, conn_buf
  var adv_ptr, conn_ptr          # C callback pointers (BLE needs cb.gen_cb, not plain closures)
  var busy                       # true while connecting / connected
  var last_rx                    # millis of last valid frame

  static WATCHDOG_MS = 60000     # no data for 60 s -> drop link and rescan

  def init(ble)
    self.ble = ble
    self.target_name = "inno-013-7985"
    self.svc_uuid    = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    self.tx_uuid     = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    self.adv_buf  = bytes(-64)   # fixed-size buffers, filled by the driver
    self.conn_buf = bytes(-64)
    self.busy = false
    self.last_rx = 0

    tasmota.cmd("MI32Option4 1") # active scanning: names arrive in scan responses

    self.adv_ptr  = inno_adv_ptr
    self.conn_ptr = inno_conn_ptr
    self.ble.conn_cb(self.conn_ptr, self.conn_buf)   # raises ble_error if BLE is not up yet

    print("InnoLuxBLE: driver started")
    tasmota.set_timer(3000, /-> self.start_scan(), "inno_scan")
    tasmota.add_cron("*/10 * * * * *", /-> self.watchdog(), "inno_wd")
  end

  # 11-byte frame: AA 0D 00 | meta[2] | float32 LE | xor | BB   (xor of bytes 0..9 == 0xFF)
  def decode_lux(f)
    if size(f) != 11 || f[0] != 0xAA || f[1] != 0x0D || f[2] != 0x00 || f[10] != 0xBB
      return nil
    end
    var x = 0
    for i: 0..9  x = x ^ f[i]  end
    if x != 0xFF  return nil  end
    var lux = f.getfloat(5)
    if math.isnan(lux) || !(lux >= 0)  return nil  end
    return lux
  end

  # adv_buf layout: MAC[6] | addr_type[1] | RSSI[1] | payload_len[1] | payload...
  def adv_name(b)
    var stop = 9 + b[8]
    if stop > size(b)  stop = size(b)  end
    var i = 9
    while i + 1 < stop
      var l = b[i]
      if l == 0  break  end
      var t = b[i + 1]
      if (t == 0x09 || t == 0x08) && (i + l < stop)
        return b[i + 2 .. i + l].asstring()
      end
      i += l + 1
    end
    return nil
  end

  def on_adv(svc, manu)
    if self.busy  return  end
    if self.adv_name(self.adv_buf) != self.target_name  return  end
    self.busy = true
    var mac = self.adv_buf[0..5]
    var addr_type = self.adv_buf[6]
    tasmota.set_timer(0, /-> self.connect(mac, addr_type))   # leave the driver callback first
  end

  def connect(mac, addr_type)
    self.ble.adv_cb(nil)
    print(string.format("InnoLuxBLE: found %s (%s), connecting...", self.target_name, mac.tohex()))
    self.ble.set_MAC(mac, addr_type)
    self.ble.set_svc(self.svc_uuid)
    self.ble.set_chr(self.tx_uuid)
    self.last_rx = tasmota.millis()
    self.ble.run(3)              # subscribe, stay connected
  end

  def on_conn(err, op, uuid, handle)
    if err != 0
      print(string.format("InnoLuxBLE: error %d (op %d), rescanning in 5 s", err, op))
      self.schedule_rescan()
      return
    end
    if op == 3
      print("InnoLuxBLE: subscribed to notifications")
    elif op == 103               # notification data
      var n = self.conn_buf[0]
      if n == 0 || n >= size(self.conn_buf)  return  end
      var lux = self.decode_lux(self.conn_buf[1 .. n])
      if lux != nil
        self.last_rx = tasmota.millis()
        global.sensors["lux"] = lux
        global.sensors["lux_ms"] = self.last_rx
        log(string.format("InnoLuxBLE: %.2f lx", lux), 3)
      end
    elif op == 5
      print("InnoLuxBLE: disconnected, rescanning in 5 s")
      self.schedule_rescan()
    end
  end

  def watchdog()
    if self.busy && tasmota.millis() - self.last_rx > self.WATCHDOG_MS
      print("InnoLuxBLE: no data, dropping link")
      self.ble.run(5)
      self.schedule_rescan()
    end
  end

  def schedule_rescan()
    tasmota.remove_timer("inno_scan")
    tasmota.set_timer(5000, /-> self.start_scan(), "inno_scan")
  end

  def start_scan()
    self.busy = false
    print("InnoLuxBLE: scanning for " + self.target_name)
    self.ble.adv_cb(self.adv_ptr, self.adv_buf)
  end
end

# =====================================================================
#  DHT20 (AHT20 inside) over Berry I2C
# =====================================================================
class DHT20
  static ADDR = 0x38
  var w

  def crc8(b, n)                 # CRC-8, poly 0x31, init 0xFF
    var c = 0xFF
    for i: 0..n-1
      c = c ^ b[i]
      for k: 0..7
        c = (c & 0x80) ? (((c << 1) ^ 0x31) & 0xFF) : ((c << 1) & 0xFF)
      end
    end
    return c
  end

  def read_raw(n)
    self.w._request_from(self.ADDR, n)
    var r = bytes()
    while self.w._available()  r.add(self.w._read(), 1)  end
    return r
  end

  def begin()
    self.w = tasmota.wire_scan(self.ADDR)
    if self.w == nil  return false  end
    var st = self.read_raw(1)
    if size(st) == 1 && (st[0] & 0x18) != 0x18     # not calibrated -> init
      self.w.write_bytes(self.ADDR, 0xBE, bytes("0800"))
    end
    return true
  end

  def trigger()                  # result ready after ~80 ms
    self.w.write_bytes(self.ADDR, 0xAC, bytes("3300"))
  end

  def read()                     # [temperature °C, humidity %RH] or nil
    var r = self.read_raw(7)
    if size(r) < 7 || (r[0] & 0x80)  return nil  end
    if self.crc8(r, 6) != r[6]  return nil  end
    var h = (r[1] << 12) | (r[2] << 4) | (r[3] >> 4)
    var t = ((r[3] & 0x0F) << 16) | (r[4] << 8) | r[5]
    return [t * 200.0 / 1048576 - 50, h * 100.0 / 1048576]
  end
end

dht = DHT20()
dht_native = nil                 # true if Tasmota's own AHT2x driver reports the sensor

def dht_cycle()
  if global.dht_native == nil
    var s = tasmota.read_sensors()
    var j = (s != nil) ? json.load(s) : nil
    global.dht_native = (j != nil && j.contains("AHT2X"))
  end
  if global.dht_native  return  end          # AHT2x driver already puts it in SENSOR
  if !global.dht_ok
    global.dht_ok = global.dht.begin()
    if !global.dht_ok  log("DHT20: not found at 0x38 (check wiring / I2C pins)", 2)  return  end
  end
  global.dht.trigger()
  tasmota.set_timer(100, def ()
    var v = global.dht.read()
    if v != nil
      global.sensors["t"] = v[0]
      global.sensors["h"] = v[1]
      global.sensors["th_ms"] = tasmota.millis()
      log(string.format("DHT20: %.1f C, %.1f %%RH", v[0], v[1]), 3)
    else
      global.dht_ok = false
    end
  end)
end
tasmota.add_cron(string.format("*/%d * * * * *", DHT_PERIOD_S), dht_cycle, "dht_read")

# =====================================================================
#  Tasmota driver: add readings to SENSOR (MQTT) and the main web page
# =====================================================================
class GatewaySensors
  static LUX_MAX_AGE_MS = 120000   # drop stale values instead of repeating them
  static TH_MAX_AGE_MS  = 60000

  def fresh(key_ms, max_age)
    var ms = global.sensors[key_ms]
    return ms != 0 && tasmota.millis() - ms < max_age
  end

  def json_append()
    var s = global.sensors
    if s["lux"] != nil && self.fresh("lux_ms", self.LUX_MAX_AGE_MS)
      tasmota.response_append(string.format(',"InnoLux":{"Illuminance":%.1f}', s["lux"]))
    end
    if s["t"] != nil && self.fresh("th_ms", self.TH_MAX_AGE_MS)
      tasmota.response_append(string.format(',"DHT20":{"Temperature":%.1f,"Humidity":%.1f}', s["t"], s["h"]))
    end
  end

  def web_sensor()
    var s = global.sensors
    if s["lux"] != nil && self.fresh("lux_ms", self.LUX_MAX_AGE_MS)
      tasmota.web_send(string.format("{s}InnoLux Illuminance{m}%.1f lx{e}", s["lux"]))
    end
    if s["t"] != nil && self.fresh("th_ms", self.TH_MAX_AGE_MS)
      tasmota.web_send(string.format("{s}DHT20 Temperature{m}%.1f &deg;C{e}", s["t"]))
      tasmota.web_send(string.format("{s}DHT20 Humidity{m}%.1f %%{e}", s["h"]))
    end
  end
end
tasmota.add_driver(GatewaySensors())

# =====================================================================
#  Setup: BLE on, I2C pins, TelePeriod; then start BLE
# =====================================================================
def ensure_config()               # returns true if a restart was issued
  var tp = tasmota.cmd("TelePeriod")
  if tp != nil && tp.find("TelePeriod") != TELE_PERIOD_S
    tasmota.cmd(string.format("TelePeriod %d", TELE_PERIOD_S))   # no restart needed
  end
  var cmds = []
  var so = tasmota.cmd("SetOption115")
  if so != nil && so.find("SetOption115") == "OFF"
    cmds.push("SetOption115 1")
  end
  if !tasmota.wire1.enabled() && !tasmota.wire2.enabled()
    cmds.push(string.format("GPIO%d 640", SDA_GPIO))      # 640 = I2C SDA
    cmds.push(string.format("GPIO%d 608", SCL_GPIO))      # 608 = I2C SCL
  end
  if size(cmds) == 0  return false  end
  print("Gateway: applying " + cmds.concat("; ") + " and restarting")
  for c: cmds  tasmota.cmd(c)  end
  tasmota.cmd("Restart 1")
  return true
end

def inno_start()                  # retry until the BLE driver is up (it starts after Wi-Fi)
  if global.innolux != nil  return  end
  try
    import BLE
    global.innolux = InnoLuxBLE(BLE)
  except .. as e, m
    print("InnoLuxBLE: BLE not ready (" + str(m) + "), retrying in 5 s")
    global.innolux = nil
    tasmota.set_timer(5000, inno_start)
  end
end

tasmota.add_rule("System#Boot", def ()
  if ensure_config()  return  end
  tasmota.set_timer(5000, inno_start)
end)
