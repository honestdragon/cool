# M5Stack Development Guide

A practical path from zero to your first working program on M5Stack (ESP32-based) devices.

---

## 1. Pick your development path

| Path | Best for | Language |
|------|----------|----------|
| **UIFlow** | Beginners, quick prototypes | Blockly / MicroPython |
| **Arduino IDE** | Most tutorials, simple C++ | C++ |
| **PlatformIO + VS Code** | Serious projects, larger codebases | C++ |
| **ESP-IDF** | Low-level, production firmware | C |

**Recommendation:** Start with **Arduino IDE** or **PlatformIO**. Both use the modern **M5Unified** library (replaces older `M5Stack.h`).

---

## 2. Hardware setup

1. **USB cable** — use a **data** cable (many charge-only cables fail).
2. **Connect** the M5Stack to your PC.
3. **Identify your exact model** (Core, Core2, StickC, Atom, CoreS3, etc.) — board selection matters.

**Serial port drivers (if the port doesn’t appear):**

- **Windows:** CP210x or CH340 driver (depends on your board)
- **Linux:** usually works; add user to `dialout`:  
  `sudo usermod -aG dialout $USER` (log out/in)
- **macOS:** usually plug-and-play

---

## 3. Arduino IDE setup (recommended first)

### Step 1 — Install Arduino IDE 2.x

Download: https://www.arduino.cc/en/software

### Step 2 — Add ESP32 board support

1. **File → Preferences**
2. Add to **Additional boards manager URLs**:

```
https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

3. **Tools → Board → Boards Manager**
4. Search **esp32**, install **esp32 by Espressif Systems**

### Step 3 — Select your M5Stack board

**Tools → Board → esp32 →** pick your device, e.g.:

- `M5Stack-Core-ESP32` (Basic / Gray / Fire)
- `M5Stack-Core2`
- `M5Stick-C`
- `M5Atom`
- `M5Stack-CoreS3`

Or use M5Stack’s board package if you installed it from their docs.

### Step 4 — Install libraries

**Sketch → Include Library → Manage Libraries**, install:

1. **M5Unified** (by lovyan03 / M5Stack)
2. **M5GFX** (installed automatically as dependency)

For Grove/Unit sensors, also install **M5UnitUnified** and the specific unit library (e.g. `M5Unit-ENV`).

### Step 5 — First program (Hello World)

```cpp
#include <M5Unified.h>

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  M5.Display.setTextSize(3);
  M5.Display.println("Hello World!");
  Serial.begin(115200);
  Serial.println("Hello World!");
}

void loop() {
  M5.update();  // update buttons, touch, etc.
  delay(1000);
}
```

1. Connect device via USB  
2. **Tools → Port** → select the COM/tty port  
3. Click **Upload**

Examples: **File → Examples → M5Unified → Basic**

---

## 4. PlatformIO setup (VS Code)

Better for multi-file projects and version control.

### Install

1. Install **VS Code**
2. Install extension **PlatformIO IDE**

### Create project

**PlatformIO → New Project**

- **Board:** your M5Stack model (e.g. `M5Stack Core ESP32`, `M5Stack Core2`)
- **Framework:** Arduino

### `platformio.ini` example

```ini
[env:m5stack-core-esp32]
platform = espressif32
board = m5stack-core-esp32
framework = arduino
monitor_speed = 115200
lib_deps =
    m5stack/M5Unified
    m5stack/M5GFX
```

### `src/main.cpp`

Same Hello World code as above.

**Upload:** PlatformIO toolbar → **Upload** (→)

Official guide: https://docs.m5stack.com/en/arduino/m5unified/intro_vscode

---

## 5. UIFlow (no-code / low-code)

1. Go to https://flow.m5stack.com/
2. Create account, select your device
3. Drag blocks or write MicroPython
4. Flash over USB or Wi‑Fi (depending on model)

Good for UI demos; less flexible than C++ for custom firmware.

---

## 6. Core concepts

### M5Unified API (unified across devices)

```cpp
M5.begin(cfg);           // Init display, power, IMU, etc.
M5.update();             // Poll buttons/touch in loop()
M5.Display.print(...);   // Screen (via M5GFX)
M5.Speaker, M5.Mic        // Audio (model-dependent)
M5.BtnA, M5.BtnB          // Buttons
M5.getPin(m5::pin_name_t::port_a_sda)  // Correct pins per board
```

Same code often runs on Core, Core2, Stick, Atom with little or no change.

### Grove / Unit modules (I2C sensors)

```cpp
#include <M5Unified.h>
#include <Wire.h>

void setup() {
  M5.begin();
  auto sda = M5.getPin(m5::pin_name_t::port_a_sda);
  auto scl = M5.getPin(m5::pin_name_t::port_a_scl);
  Wire.begin(sda, scl, 400000);
  // Then use sensor library...
}
```

I2C address table: https://docs.m5stack.com/en/product_i2c_addr

### Wi‑Fi (standard ESP32)

M5Unified doesn’t wrap Wi‑Fi — use ESP32 Arduino APIs:

```cpp
#include <WiFi.h>

WiFi.begin("SSID", "password");
while (WiFi.status() != WL_CONNECTED) delay(500);
```

---

## 7. Project structure tips

```
my-m5-project/
├── src/main.cpp          # PlatformIO entry
├── platformio.ini        # Board + libs
└── lib/                  # Your modules
```

**Typical loop pattern:**

```cpp
void loop() {
  M5.update();
  if (M5.BtnA.wasPressed()) { /* ... */ }
  // read sensors, update display
  delay(10);
}
```

---

## 8. Debugging

| Tool | Use |
|------|-----|
| **Serial Monitor** | `Serial.println()` at 115200 baud |
| **M5.Log** | Built-in logging in M5Unified |
| **Examples** | M5Unified → Basic → HowToUse |

**Common issues:**

- **Upload fails** → hold **RESET**, tap **BOOT** (model-specific), retry upload
- **Blank screen** → wrong board selected in Tools/Board
- **Port missing** → bad cable or missing USB driver
- **Library errors** → install M5GFX + M5Unified together

---

## 9. Official resources

| Resource | URL |
|----------|-----|
| Developer docs | https://docs.m5stack.com/en/start |
| M5Unified Hello World | https://docs.m5stack.com/en/arduino/m5unified/helloworld |
| PlatformIO guide | https://docs.m5stack.com/en/arduino/m5unified/intro_vscode |
| M5Unified GitHub | https://github.com/m5stack/M5Unified |
| M5GFX GitHub | https://github.com/m5stack/M5GFX |
| Examples | https://github.com/m5stack/M5Unified/tree/master/examples |
| FAQ | https://faq.m5stack.com |
| Forum | https://community.m5stack.com |

---

## 10. Learning path (suggested order)

1. Flash **Hello World** (screen + serial)
2. Read button input (`M5.BtnA.wasPressed()`)
3. Draw on screen (M5GFX shapes, text, colors)
4. Connect a **Grove Unit** (ENV, IMU, etc.)
5. Add **Wi‑Fi** (HTTP / MQTT)
6. Use **deep sleep** for battery projects (StickC, Atom)

---

If you tell me your **exact model** (e.g. Core2, StickC Plus 2, AtomS3), I can give you a tailored `platformio.ini`, board selection, and a first project (display + Wi‑Fi + sensor).


# M5Stack + MicroPython Development Guide

On M5Stack, **MicroPython today means UIFlow 2 firmware** — official M5Stack MicroPython with display, buttons, Wi‑Fi, and Unit drivers built in. You write Python directly; Blockly is optional.

---

## Two ways to write code

| Mode | What it is |
|------|------------|
| **Code (MicroPython)** | Write `.py` scripts — what you want |
| **Blockly** | Visual blocks that generate Python — optional |

You can use **pure MicroPython** even if you flash UIFlow2 firmware.

---

## Step 1 — Install tools

1. **M5Burner** (flash firmware)  
   https://docs.m5stack.com/en/download  
   Windows / macOS / Linux

2. **M5Stack account** (free)  
   Same account for M5Burner and UIFlow2 web IDE

3. **Optional local IDE**
   - **UIFlow2 Web IDE** (browser) — easiest
   - **Thonny** — local editor + REPL over USB
   - **VS Code + mpremote** — advanced local workflow

---

## Step 2 — Flash UIFlow2 firmware

1. Connect M5Stack via **USB data cable**
2. Put device in **download mode** (varies by model — check your product doc)
3. Open **M5Burner** → log in
4. Find **UIFlow2** firmware for **your exact model** (Core, Core2, StickC, AtomS3, etc.)
5. Click **Burn** → select serial port
6. Enter **Wi‑Fi SSID + password** (needed for cloud IDE; optional if you only use USB)
7. Wait for burn to finish → device reboots

Product-specific guides: https://docs.m5stack.com/en/uiflow2/uiflow_web

---

## Step 3 — Connect and run code

### Option A — UIFlow2 Web IDE (recommended to start)

1. Open https://uiflow2.m5stack.com/
2. Log in (same account as M5Burner)
3. Connect device:
   - **Wi‑Fi:** device appears under “Select Device” if Wi‑Fi was configured
   - **USB:** click **WebTerminal** → pick serial port → “Connected to Serial Port!”
4. Switch to **Code** tab (not Blockly)
5. Write Python → **Run** or **Download** to device

### Option B — Thonny (local, offline-friendly)

1. Install Thonny: https://thonny.org/
2. **Run → Select interpreter → MicroPython (ESP32)**
3. Select your USB port
4. You get a REPL; save files as `main.py` on the device filesystem

### Option C — mpremote (CLI)

```bash
pip install mpremote
mpremote connect
mpremote cp main.py :main.py
mpremote reset
```

---

## Step 4 — Your first MicroPython program

Standard UIFlow2 pattern: `setup()` + `loop()` + `M5.update()`.

```python
import M5
from M5 import *

def setup():
    M5.begin()
    Widgets.fillScreen(0x222222)
    M5.Lcd.setTextColor(0xFFFFFF, 0x222222)
    M5.Lcd.setCursor(10, 10)
    M5.Lcd.print("Hello MicroPython!")

def loop():
    M5.update()  # refresh buttons, touch, etc.

if __name__ == "__main__":
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        print(e)
```

---

## Step 5 — Core API cheat sheet

```python
import M5
from M5 import *

M5.begin()              # init display, power, etc.
M5.update()             # call every loop

# Display
M5.Lcd.fillScreen(0x000000)
M5.Lcd.setCursor(x, y)
M5.Lcd.print("text")
M5.Lcd.printf("count: %d", n)
M5.Lcd.drawCircle(x, y, r, color)
M5.Lcd.drawRect(x, y, w, h, color)

# Widgets (higher-level UI)
Widgets.Label("Hi", x, y, 1.0, fg, bg, Widgets.FONTS.DejaVu18)

# Buttons (model-dependent)
if BtnA.wasPressed():
    print("A pressed")

# Wi-Fi
import network
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect("SSID", "password")
while not wlan.isconnected():
    pass
print(wlan.ifconfig())
```

Full API docs: https://uiflow-micropython.readthedocs.io/

---

## Step 6 — Auto-run on boot

Save your program on the device as:

| File | Purpose |
|------|---------|
| `main.py` | Runs automatically on boot |
| `boot.py` | Runs first (Wi‑Fi setup, etc.) |

In UIFlow2 IDE: **Download to device** → firmware runs it on restart.

Minimal boot flow:

```python
# boot.py — optional Wi-Fi setup
import network
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect("SSID", "password")
```

```python
# main.py — your app
import M5
from M5 import *

M5.begin()
M5.Lcd.print("Running from main.py")
```

---

## Step 7 — Grove / Unit sensors

```python
from hardware import I2C, Pin
from unit import ENVUnit  # example: ENV sensor

M5.begin()
i2c = I2C(0, scl=Pin(1), sda=Pin(2), freq=100000)
env = ENVUnit(i2c)

while True:
    M5.update()
    temp, hum, press = env.getValues()
    print(temp, hum, press)
```

I2C addresses: https://docs.m5stack.com/en/product_i2c_addr

Each Unit has examples in the docs under **MicroPython Example**.

---

## Important notes

### Don’t use generic ESP32 MicroPython alone

Stock MicroPython from micropython.org **does not** include M5Stack display/button drivers. Use **UIFlow2 firmware** (or build from `uiflow-micropython`).

### UIFlow2 vs “plain” MicroPython

- UIFlow2 firmware **is** MicroPython + M5 libraries
- Cloud login is for the web IDE; you can still write local `.py` files
- For fully offline/cloud-free use, build custom firmware from:  
  https://github.com/m5stack/uiflow-micropython

### Old repo is deprecated

Ignore `M5Stack_MicroPython` — use **uiflow-micropython** instead.

---

## Recommended learning path

1. Flash UIFlow2 with M5Burner  
2. Run Hello World in Web IDE (Code mode)  
3. Read button input (`BtnA`, `BtnB`)  
4. Add Wi‑Fi + HTTP/MQTT  
5. Connect one Grove Unit  
6. Save as `main.py` for standalone operation  

---

## Official links

| Resource | URL |
|----------|-----|
| UIFlow2 getting started | https://docs.m5stack.com/en/uiflow2/uiflow_web |
| MicroPython API reference | https://uiflow-micropython.readthedocs.io/ |
| M5Burner download | https://docs.m5stack.com/en/download |
| Firmware source | https://github.com/m5stack/uiflow-micropython |
| Community forum | https://community.m5stack.com |

---

Tell me your **exact M5Stack model** (e.g. Core2, StickC Plus 2, AtomS3) and I can give you:

- download-mode steps for that board  
- a ready-to-flash Hello World  
- Wi‑Fi + sensor example tailored to your hardware