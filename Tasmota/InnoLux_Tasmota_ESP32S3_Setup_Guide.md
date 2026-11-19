# Yolo-Uno (ESP32-S3) Tasmota Gateway: BLE Light Sensor + DHT20 → CoreIoT

This guide sets up a Yolo-Uno board running Tasmota as a gateway that:

- connects to the **InnoLab BLE light sensor** (`inno-013-7985`) over Bluetooth Low Energy and reads illuminance, and
- reads the **DHT20** temperature/humidity sensor over I2C,

then publishes both to **CoreIoT** over MQTT.

| Item | Value |
| :--- | :--- |
| Gateway | Yolo-Uno, ESP32-S3 (QFN56, 8 MB embedded PSRAM, USB-Serial/JTAG) |
| Light sensor | InnoLab BLE, name `inno-013-7985`, Nordic UART Service (notifications) |
| Temp/humidity sensor | DHT20 (AHT20 inside), I2C address `0x38`, on GPIO11 (SDA) / GPIO12 (SCL) |
| Firmware | Tasmota **v16.0.0**, custom build `tasmota32s3-mi32` |
| Cloud | CoreIoT (ThingsBoard MQTT), `app.coreiot.io:1883`, topic `v1/devices/me/telemetry` |
| Telemetry | `{"lux":…}`, `{"temperature":…,"humidity":…}` |
| Host PC | Windows, PowerShell, PlatformIO Core 6.2.0 (Python 3.11) |

---

## Contents

1. [How it works](#1-how-it-works)
2. [Prerequisites](#2-prerequisites)
3. [Compile the firmware](#3-compile-the-firmware)
4. [Flash the firmware](#4-flash-the-firmware)
5. [Wire the DHT20](#5-wire-the-dht20)
6. [Configure Wi-Fi and MQTT](#6-configure-wi-fi-and-mqtt)
7. [Upload the script](#7-upload-the-script)
8. [Verify](#8-verify)
9. [Troubleshooting](#9-troubleshooting)
10. [Appendix A: `platformio_tasmota_cenv.ini`](#appendix-a-platformio_tasmota_cenvini)
11. [Appendix B: `autoexec.be`](#appendix-b-autoexecbe)
12. [Appendix C: `user_config_override.h` (optional)](#appendix-c-user_config_overrideh-optional)

---

## 1. How it works

### What has to be in the firmware, and what is in the script

| Part | Where it lives | Why |
| :--- | :--- | :--- |
| BLE GATT client (Berry `BLE` module) | **Firmware** (custom build) | The light sensor sends readings only after a GATT connection and a notification subscription. Only the *legacy MI32* driver (`USE_MI_ESP32` **without** `USE_BLE_ESP32`) gives Berry this ability. No official ESP32-S3 binary includes it. |
| DHT20 reading | **Script** (`autoexec.be`) | Every Tasmota build has I2C access from Berry (`tasmota.wire1`). The script talks to the AHT20 chip directly, so no firmware option is needed. |
| Turning on BLE (`SetOption115`) and setting the I2C pins | **Script** | On boot, the script checks both. If either is missing, it sets both and restarts once. |
| Wi-Fi and MQTT | Entered once in the web page (or built in, see [Appendix C](#appendix-c-user_config_overrideh-optional)) | Stored in the board's settings. |

So you build the firmware **once**. Everything else is handled by `autoexec.be`.

### Light sensor protocol

- Service `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART); notify characteristic `6e400003-b5a3-f393-e0a9-e50e24dcca9e`.
- 11-byte frame:

| Bytes | Content |
| :--- | :--- |
| 0–2 | Header `AA 0D 00` |
| 3–4 | Metadata / command |
| 5–8 | Illuminance, IEEE-754 float32, little-endian (lux) |
| 9 | Checksum: XOR of bytes 0–9 = `0xFF` |
| 10 | Footer `BB` |

### DHT20 protocol (as used by the script)

1. Write `AC 33 00` to address `0x38` to start a measurement.
2. Wait 100 ms, then read 7 bytes: status, 20-bit humidity, 20-bit temperature, CRC-8 (poly `0x31`, init `0xFF`).
3. Humidity = raw / 2²⁰ × 100 %RH; temperature = raw / 2²⁰ × 200 − 50 °C.

---

## 2. Prerequisites

- **Git** for Windows.
- **PlatformIO Core 6.2.0**, through the VS Code PlatformIO extension or `pip install platformio==6.2.0` (Python 3.11–3.13).
- A **USB data cable** for the Yolo-Uno.
- The 2.4 GHz **Wi-Fi name and password**.
- A **CoreIoT device** and its **access token** (CoreIoT → *Devices* → your device → *Copy access token*).
- A **DHT20** module and four jumper wires.

If `pio` isn't recognised in PowerShell, add it to `PATH` for the current window:

```powershell
$env:Path = "$env:USERPROFILE\.platformio\penv\Scripts;$env:Path"
pio --version          # must print: PlatformIO Core, version 6.2.0
```

To make it permanent, add `C:\Users\<you>\.platformio\penv\Scripts` to your user `Path` (Start → *Edit environment variables for your account*).

---

## 3. Compile the firmware

### 3.1 Clone Tasmota

```powershell
cd D:\
git clone --depth 1 --branch v16.0.0 https://github.com/arendst/Tasmota.git
cd D:\Tasmota
```

### 3.2 Add the build profile

Copy **`platformio_tasmota_cenv.ini`** ([Appendix A](#appendix-a-platformio_tasmota_cenvini)) into `D:\Tasmota\`, next to `platformio.ini`.

- This is Tasmota's own sample profile `tasmota32s3-mi32`, with the I2S-audio and ULP flags removed (the audio flag breaks the build).
- Leave out any `platformio_override.ini`. A `user_config_override.h` is **optional** ([Appendix C](#appendix-c-user_config_overrideh-optional)). It must never contain `#define USE_BLE_ESP32`, because that removes the Berry `BLE` module.

### 3.3 Build

1. **Close VS Code completely.** Its PlatformIO extension indexes the project in the background and locks `tasmota\tasmota.ino.cpp`.
2. In a plain PowerShell:

```powershell
cd D:\Tasmota
pio run -e tasmota32s3-mi32
```

The first build downloads the platform and toolchains (about 10–15 min). Later builds take a few minutes.

### 3.4 Output

In `D:\Tasmota\.pio\build\tasmota32s3-mi32\`:

| File | Use |
| :--- | :--- |
| `firmware.factory.bin` | Full image (bootloader, partition table, Safeboot, Tasmota), about 2.6 MB. Flash over **USB at `0x0`**. |
| `firmware.bin` | Application only. Use for an **OTA upgrade** from the Tasmota web page. |

To check the factory image (the first byte must be `E9`):

```powershell
Format-Hex .pio\build\tasmota32s3-mi32\firmware.factory.bin -Count 16
```

---

## 4. Flash the firmware

### Option A: USB (first install, recovery, or replacing other firmware)

1. Close VS Code, serial monitors and web-flasher tabs so the COM port is free.
2. Find the port in Device Manager → *Ports (COM & LPT)*. This guide uses **COM3**.
3. Put the board in **download mode**: hold **BOOT**, press and release **RESET**, release **BOOT**.
4. Erase, then write:

```powershell
$py  = "$env:USERPROFILE\.platformio\penv\Scripts\python.exe"
$bin = "D:\Tasmota\.pio\build\tasmota32s3-mi32\firmware.factory.bin"

& $py -m esptool --chip esp32s3 --port COM3 erase-flash
# enter download mode again if the next command cannot connect
& $py -m esptool --chip esp32s3 --port COM3 write-flash 0x0 $bin
```

5. Wait for `Hash of data verified.`, then press **RESET**.

> **esptool v5:** commands use hyphens (`erase-flash`, `write-flash`). `--verify` has been removed because every write is checked automatically.
>
> **Use esptool, not a web page, for local files.** A web flasher given the wrong input can write an HTML page to the board, which then fails to boot with `invalid header: 0x4f44213c`.

### Option B: OTA (the board already runs Tasmota)

1. Web page → **Firmware Upgrade** → **Upgrade by file upload** → choose `firmware.bin` → **Start upgrade**.
2. If it reboots into **Safeboot** (normal with a 4 MB layout), upload the same `firmware.bin` again.

OTA keeps Wi-Fi, MQTT and the file system. A USB `erase-flash` clears all of them.

---

## 5. Wire the DHT20

DHT20 pins, left to right with the grille facing you: **VDD, SDA, GND, SCL**.

| DHT20 | Yolo-Uno |
| :--- | :--- |
| VDD | 3V3 |
| SDA | GPIO11 (SDA) |
| GND | GND |
| SCL | GPIO12 (SCL) |

GPIO11/GPIO12 are the I2C pins used in Yolo-Uno examples. Check them against the labels on your board or its I2C/Grove connector. If yours differ, change `SDA_GPIO` and `SCL_GPIO` near the top of the DHT20 section in `autoexec.be`. Use 3V3, not 5 V.

---

## 6. Configure Wi-Fi and MQTT

### 6.1 Wi-Fi

**Through the access point:** after a fresh flash, connect to the Wi-Fi network `tasmota-XXXXXX-XXXX`, open `http://192.168.4.1`, enter your Wi-Fi details and save. The board restarts and joins your network. Find its IP address on your router.

**Through the serial console** (if the access point doesn't appear):

```powershell
pio device monitor -b 115200
```
```
Backlog SSID1 YourWifiName; Password1 YourPassword
```

The IP address appears in the log after the restart.

### 6.2 MQTT for CoreIoT

Web page → **Configuration** → **Configure MQTT**:

| Field | Value |
| :--- | :--- |
| Host | `app.coreiot.io` (or your CoreIoT server) |
| Port | `1883` |
| Client | leave default |
| User | `<YOUR_COREIOT_DEVICE_ACCESS_TOKEN>` |
| Password | *(empty)* |
| Topic | `devices/me` |
| Full Topic | leave default |

Click **Save**, or enter the same settings in **Consoles → Console** (a lone `"` sets an empty value):

```
Backlog MqttHost app.coreiot.io; MqttPort 1883; MqttUser <TOKEN>; MqttPassword "; Topic devices/me
```

The console should show `MQT: Connected`.

Bluetooth and the I2C pins don't need setting by hand. The script handles them in the next step.

---

## 7. Upload the script

1. Web page → **Consoles** → **Manage File system**.
2. Upload **`autoexec.be`** ([Appendix B](#appendix-b-autoexecbe)), replacing any older copy.
3. Run `Restart 1` in the console.

On the first boot, the script prints something like:

```text
Gateway: applying SetOption115 1; GPIO11 640; GPIO12 608 and restarting
```

and restarts once. From then on it runs without further intervention.

### What the script does

**Setup (each boot)**
- If `SetOption115` (BLE) is OFF or no I2C bus is configured, it sets `SetOption115 1` and `GPIO11 640` (SDA) / `GPIO12 608` (SCL), then restarts once.
- It retries `import BLE` every 5 s until the BLE driver is up. The driver starts only after Wi-Fi connects.

**Light sensor**
- Turns on active scanning (`MI32Option4 1`) and looks for `inno-013-7985`.
- Connects and subscribes to `6e400003-…`.
- On each notification (`op 103`), checks the frame (header, XOR checksum, footer), reads the float, and publishes `{"lux":<value>}`.
- On error, disconnect, or 60 s without data, it disconnects and scans again.

**DHT20**
- Every 10 s (`DHT_PERIOD_S`), measures over I2C, checks the CRC, and publishes `{"temperature":<°C>,"humidity":<%RH>}`.
- Runs independently of BLE, so temperature and humidity keep coming in while the light sensor is offline.
- If the firmware also has Tasmota's AHT2x driver ([Appendix C](#appendix-c-user_config_overrideh-optional)), it uses that driver's readings instead.

---

## 8. Verify

### Console

Turn on the light sensor and open **Consoles → Console**:

```text
InnoLuxBLE: BLE not ready (BLE: device not initialized), retrying in 5 s   <- may appear once or twice
InnoLuxBLE: driver started
InnoLuxBLE: scanning for inno-013-7985
InnoLuxBLE: found inno-013-7985 (xxxxxxxxxxxx), connecting...
InnoLuxBLE: subscribed to notifications
InnoLuxBLE -> {"lux":145.20}
DHT20 -> {"temperature":28.4,"humidity":71.3}
```

### Checks

| Check | Command / place | Expected |
| :--- | :--- | :--- |
| BLE on | `SetOption115` | `ON` |
| BLE driver started | console log after boot | `M32: Init BLE device: …` |
| Berry BLE module | Berry console: `import BLE print(BLE)` (only after the line above) | `<module: BLE>` |
| DHT20 on the bus | `I2CScan` | device at `0x38` |
| MQTT | console log | `MQT: Connected` |
| Firmware | *Information → Program version* | `bluetooth` image |

### CoreIoT

CoreIoT → **Devices** → your device → **Latest telemetry**: `lux`, `temperature` and `humidity` should be updating.

---

## 9. Troubleshooting

### Build

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `Failed to install Python dependencies into penv` | PlatformIO's private Python environment couldn't install the platform's packages (the real error is hidden). | Install them by hand to see the error: `& "$env:USERPROFILE\.platformio\penv\Scripts\python.exe" -m pip install <packages>`. Use Python 3.11–3.13. Don't build from an activated project `.venv`. |
| `IncompatiblePlatform … PlatformIO Core v6.2.0-post.1 … depends on >=6.2.0` | `pioarduino 6.2.0.post1` replaced the core, and its version string counts as older than 6.2.0. | `& $py -m pip install --force-reinstall --no-deps "pioarduino==6.2.0"`, then `pio --version` should show 6.2.0. |
| `RequestsDependencyWarning: … charset_normalizer` | Missing optional package. Harmless. | `& $py -m pip install charset-normalizer` |
| `PermissionError: [Errno 13] … tasmota.ino.cpp` | VS Code's PlatformIO extension or antivirus has the file locked. | Close VS Code, stop leftover `python`/`pio` processes, delete `tasmota\tasmota.ino.cpp`, rebuild. Add `D:\Tasmota` and `%USERPROFILE%\.platformio` to Defender exclusions. |
| `fatal error: AudioOutput.h: No such file or directory` | `-DUSE_I2S_ALL` turns on audio, but the audio library isn't on the include path. | Remove `-DUSE_I2S_ALL` (and `-DUSE_BERRY_ULP`). Appendix A already has them removed. |
| `pio` not recognised | Not on `PATH`. | See [Prerequisites](#2-prerequisites). |

### Flashing and boot

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `The port is already open` / esptool can't connect | Port in use, or board not in download mode. | Close other programs. Hold BOOT, tap RESET, release BOOT. Try another cable or port. |
| `No such option: --verify` | esptool v5. | Leave it out. |
| `invalid header: 0xffffffff` | Nothing at `0x0`, or the write didn't finish. | Erase and write `firmware.factory.bin` at `0x0` again. |
| `invalid header: 0x4f44213c` | Those bytes spell `<!DO`: an **HTML page** was flashed. | Check the file starts with `E9` and flash it with esptool and the full path. |
| No `tasmota-…` access point | Still in download mode, or old Wi-Fi settings kept. | Press RESET. Check your router. Set Wi-Fi through the serial console. |
| Boot loop with PSRAM / `Guru Meditation` errors | PSRAM type mismatch (the board has octal PSRAM). | Capture the serial log. Change the `board =` line to an octal-PSRAM profile and rebuild. |

### Runtime

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `import_error - module 'BLE' not found` | Firmware without the legacy MI32 driver (e.g. stock `tasmota32s3`). | Flash this custom build. |
| `ble_error - BLE: device not initialized` (repeating) | BLE driver not started: `SetOption115` OFF, or Wi-Fi not connected. | Connect Wi-Fi. The script sets `SetOption115` itself. Wait for `M32: Init BLE device`. |
| `syntax_error: strict: redefinition of builtin 'bytes'` | `import bytes` in a script. | Remove it (`bytes` is built in). |
| `syntax_error: 'string' undeclared` | Missing import. | Add `import string`. |
| Stuck on `scanning` | Sensor off or out of range, or a different name. | Check with nRF Connect. Edit `target_name` in the script. |
| `error N (op M)` while connecting | GATT error. | Check the service/characteristic UUIDs in nRF Connect. Note the numbers for debugging. |
| `subscribed` but no `->` lines | The sensor may need a start command on RX `6e400002-…`. | Check the sensor's docs or app. |
| `DHT20: not found at 0x38` | Wiring, or wrong pins. | `I2CScan` must show `0x38`. Check 3V3/GND, SDA/SCL not swapped, and `SDA_GPIO`/`SCL_GPIO` match your board. |
| `I2CScan` finds nothing | I2C pins not set or wrong. | `Backlog GPIO11 640; GPIO12 608; Restart 1` (adjust GPIOs). |
| MQTT keeps reconnecting | Wrong token, or CoreIoT rejects Tasmota's own `tele/…`/`stat/…` messages. | Check the token. Look at the `MQT:` log lines. |

---

## Appendix A: `platformio_tasmota_cenv.ini`

Place in `D:\Tasmota\` (repository root). **Required.**

```ini
; Place in the ROOT of the cloned Tasmota repo (next to platformio.ini).
; Build with:  pio run -e tasmota32s3-mi32
;
; Copied from Tasmota's own platformio_tasmota_cenv_sample.ini (v16.0.0).
; FIRMWARE_BLUETOOTH + USE_MI_EXT_GUI  => legacy MI32 driver (USE_MI_ESP32 without
; USE_BLE_ESP32), which is the only driver that provides the Berry `BLE` module.

[platformio]
default_envs                = tasmota32s3-mi32

[env:tasmota32s3-mi32]
extends                     = env:tasmota32_base
board                       = esp32s3-qio_qspi
build_flags                 = ${env:tasmota32_base.build_flags}
                              -DFIRMWARE_BLUETOOTH
                              -DUSE_MI_EXT_GUI
                              -DCONFIG_BT_NIMBLE_NVS_PERSIST=y
                              -DOTA_URL='""'
lib_extra_dirs              = lib/libesp32, lib/libesp32_div, lib/lib_basic, lib/lib_i2c, lib/lib_div, lib/lib_ssl
lib_ignore                  = Micro-RTSP
```

## Appendix B: `autoexec.be`

Upload to the board's file system as `/autoexec.be`. **Required.**

```berry
# Yolo-Uno (ESP32-S3) Tasmota gateway -> CoreIoT (v1/devices/me/telemetry)
#   - InnoLab BLE light sensor (inno-013-7985) via legacy MI32 Berry BLE  -> {"lux":..}
#   - DHT20 (AHT20, I2C 0x38) via Berry I2C (or Tasmota AHT2x if built in) -> {"temperature":..,"humidity":..}
# Firmware: env tasmota32s3-mi32 (needed for BLE). DHT20 is read directly over Berry I2C,
# so no extra firmware option is needed. SetOption115 and the I2C pins are set automatically.

import mqtt
import math
import string
import cb
import global
import json

# Globals declared up front (Berry strict mode); callback pointers created ONCE
innolux = nil
dht = nil
dht_ok = false
inno_adv_ptr  = cb.gen_cb(/svc, manu -> (global.innolux != nil ? global.innolux.on_adv(svc, manu) : nil))
inno_conn_ptr = cb.gen_cb(/err, op, uuid, handle -> (global.innolux != nil ? global.innolux.on_conn(err, op, uuid, handle) : nil))

class InnoLuxCoreIoT
  var ble                        # BLE module, imported once the stack is up
  var target_name, svc_uuid, tx_uuid
  var adv_buf, conn_buf
  var adv_ptr, conn_ptr          # C callback pointers (BLE needs cb.gen_cb, not plain closures)
  var busy                       # true while connecting / connected
  var last_rx                    # millis of last valid frame

  static TELEMETRY_TOPIC = "v1/devices/me/telemetry"
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

    # Active scanning so the device name in the scan response is received
    tasmota.cmd("MI32Option4 1")

    # Callback pointers are created once at file level (gen_cb has limited slots)
    self.adv_ptr  = inno_adv_ptr
    self.conn_ptr = inno_conn_ptr
    self.ble.conn_cb(self.conn_ptr, self.conn_buf)   # raises ble_error if BLE is not up yet

    print("InnoLuxBLE: driver started")
    tasmota.set_timer(3000, /-> self.start_scan(), "inno_scan")
    tasmota.add_cron("*/10 * * * * *", /-> self.watchdog(), "inno_wd")
  end

  # ---------- frame decoding ----------
  def decode_lux(f)
    if size(f) != 11 || f[0] != 0xAA || f[1] != 0x0D || f[2] != 0x00 || f[10] != 0xBB
      return nil
    end
    var x = 0
    for i: 0..9  x = x ^ f[i]  end
    if x != 0xFF  return nil  end
    var lux = f.getfloat(5)      # little-endian IEEE-754 float at offset 5
    if math.isnan(lux) || !(lux >= 0)  return nil  end
    return lux
  end

  # ---------- advertisement handling ----------
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
    # Leave the driver callback before launching the connection
    tasmota.set_timer(0, /-> self.connect(mac, addr_type))
  end

  def connect(mac, addr_type)
    self.ble.adv_cb(nil)              # stop listening while connected
    print(string.format("InnoLuxBLE: found %s (%s), connecting...", self.target_name, mac.tohex()))
    self.ble.set_MAC(mac, addr_type)
    self.ble.set_svc(self.svc_uuid)
    self.ble.set_chr(self.tx_uuid)
    self.last_rx = tasmota.millis()
    self.ble.run(3)                   # subscribe, stay connected
  end

  # ---------- connection / GATT events ----------
  def on_conn(err, op, uuid, handle)
    if err != 0
      print(string.format("InnoLuxBLE: error %d (op %d), rescanning in 5 s", err, op))
      self.schedule_rescan()
      return
    end
    if op == 3
      print("InnoLuxBLE: subscribed to notifications")
    elif op == 103               # notification data arrived
      var n = self.conn_buf[0]
      if n == 0 || n >= size(self.conn_buf)  return  end
      var lux = self.decode_lux(self.conn_buf[1 .. n])
      if lux != nil
        self.last_rx = tasmota.millis()
        var payload = string.format('{"lux":%.2f}', lux)
        mqtt.publish(self.TELEMETRY_TOPIC, payload)
        print("InnoLuxBLE -> " + payload)
      end
    elif op == 5
      print("InnoLuxBLE: disconnected, rescanning in 5 s")
      self.schedule_rescan()
    end
  end

  def watchdog()
    if self.busy && tasmota.millis() - self.last_rx > self.WATCHDOG_MS
      print("InnoLuxBLE: no data, dropping link")
      self.ble.run(5)                 # disconnect
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

# ---------- DHT20 (AHT20 inside, I2C 0x38) -> CoreIoT, independent of BLE ----------
# Works on the current firmware: talks to the sensor directly over Berry I2C (tasmota.wire1/2).
# If the firmware also has Tasmota's AHT2x driver, its readings are used instead.
var DHT_PERIOD_S = 10
var SDA_GPIO = 11              # Yolo-Uno I2C pins (check your board's labels)
var SCL_GPIO = 12

class DHT20
  static ADDR = 0x38
  var w

  def crc8(b, n)               # CRC-8, poly 0x31, init 0xFF
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

  def trigger()                # start a measurement; result ready after ~80 ms
    self.w.write_bytes(self.ADDR, 0xAC, bytes("3300"))
  end

  def read()                   # returns [temperature, humidity] or nil
    var r = self.read_raw(7)
    if size(r) < 7 || (r[0] & 0x80)  return nil  end          # short read or busy
    if self.crc8(r, 6) != r[6]  return nil  end
    var h = (r[1] << 12) | (r[2] << 4) | (r[3] >> 4)
    var t = ((r[3] & 0x0F) << 16) | (r[4] << 8) | r[5]
    return [t * 200.0 / 1048576 - 50, h * 100.0 / 1048576]
  end
end

dht = DHT20()

def dht_send(t, h)
  if !mqtt.connected()  return  end
  var payload = string.format('{"temperature":%.1f,"humidity":%.1f}', t, h)
  mqtt.publish("v1/devices/me/telemetry", payload)
  print("DHT20 -> " + payload)
end

def dht_cycle()
  # 1) Tasmota AHT2x driver present (custom build with USE_AHT2x)?
  var s = tasmota.read_sensors()
  var j = (s != nil) ? json.load(s) : nil
  if j != nil && j.contains("AHT2X")
    var a = j["AHT2X"]
    if a.contains("Temperature") && a.contains("Humidity")
      dht_send(a["Temperature"], a["Humidity"])
    end
    return
  end
  # 2) Otherwise read the sensor directly over I2C
  if !global.dht_ok
    global.dht_ok = global.dht.begin()
    if !global.dht_ok  print("DHT20: not found at 0x38 (check wiring / I2C pins)")  return  end
  end
  global.dht.trigger()
  tasmota.set_timer(100, def ()
    var v = global.dht.read()
    if v != nil  dht_send(v[0], v[1])  else  global.dht_ok = false  end
  end)
end
tasmota.add_cron(string.format("*/%d * * * * *", DHT_PERIOD_S), dht_cycle, "dht_pub")

# ---------- One-time config: BLE on + I2C pins, then a single restart ----------
def ensure_config()
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

# ---------- BLE start: enable driver if needed, then retry until the stack is up ----------
def inno_start()
  if global.innolux != nil  return  end
  try
    import BLE                   # raises ble_error until Wi-Fi is up and the BLE driver has started
    global.innolux = InnoLuxCoreIoT(BLE)
  except .. as e, m
    print("InnoLuxBLE: BLE not ready (" + str(m) + "), retrying in 5 s")
    global.innolux = nil
    tasmota.set_timer(5000, inno_start)
  end
end
tasmota.add_rule("System#Boot", def ()
  if ensure_config()  return  end          # restarting to apply settings
  tasmota.set_timer(5000, inno_start)
end)
```

## Appendix C: `user_config_override.h` (optional)

Not needed for the gateway to work. Place it in `D:\Tasmota\tasmota\` and rebuild only if you want:

- **Wi-Fi and CoreIoT built in**, so a freshly flashed board connects with no web setup (uncomment and fill in the blocks; don't share a firmware file that has your token built in);
- **first-boot commands** that apply BLE, active scanning and the I2C pins straight away;
- **Tasmota's AHT2x driver**, which shows the DHT20 on the main web page and in Tasmota's own sensor messages. The script then uses its readings.

Defaults and `USER_BACKLOG` apply only after a fresh flash (`erase-flash`) or `Reset 1`, not after an OTA upgrade.

```cpp
/*
  user_config_override.h  ->  place in D:\Tasmota\tasmota\
  Used together with platformio_tasmota_cenv.ini (env tasmota32s3-mi32).

  Firmware defaults for the Yolo-Uno gateway:
    - AHT2x driver for the DHT20 (an AHT20 inside, I2C address 0x38)
    - First-boot commands: enable BLE, active scan, I2C on GPIO11 (SDA) / GPIO12 (SCL)
    - Optional: Wi-Fi and CoreIoT MQTT defaults (uncomment and fill in)

  Defaults and USER_BACKLOG apply on a fresh flash (after erase-flash) or after `Reset 1`.
  An OTA upgrade keeps the existing settings, so they are not applied then.
*/

#ifndef _USER_CONFIG_OVERRIDE_H_
#define _USER_CONFIG_OVERRIDE_H_

// ---- DHT20 / AHT20 temperature & humidity (I2C 0x38) ----
#ifndef USE_I2C
  #define USE_I2C
#endif
#define USE_AHT2x

// ---- Commands run once on first boot ----
//  SetOption115 1  : enable the MI32 BLE driver (needed for the Berry BLE module)
//  MI32Option4 1   : active BLE scanning (device names come in scan responses)
//  GPIO11 640      : GPIO11 = I2C SDA   (640 = GPIO_I2C_SDA)
//  GPIO12 608      : GPIO12 = I2C SCL   (608 = GPIO_I2C_SCL)
//  Restart 1       : apply BLE + GPIO changes
#undef  USER_BACKLOG
#define USER_BACKLOG "SetOption115 1;MI32Option4 1;GPIO11 640;GPIO12 608;Restart 1"

// ---- Optional: Wi-Fi defaults (uncomment and fill in) ----
// #undef  STA_SSID1
// #define STA_SSID1      "YourWifiName"
// #undef  STA_PASS1
// #define STA_PASS1      "YourWifiPassword"

// ---- Optional: CoreIoT MQTT defaults (uncomment and fill in) ----
// #undef  MQTT_HOST
// #define MQTT_HOST      "app.coreiot.io"
// #undef  MQTT_PORT
// #define MQTT_PORT      1883
// #undef  MQTT_USER
// #define MQTT_USER      "YOUR_COREIOT_DEVICE_ACCESS_TOKEN"
// #undef  MQTT_PASS
// #define MQTT_PASS      ""
// #undef  MQTT_TOPIC
// #define MQTT_TOPIC     "devices/me"

#endif  // _USER_CONFIG_OVERRIDE_H_
```
