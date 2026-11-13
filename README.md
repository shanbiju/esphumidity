# ESP32 + DHT11 Live Humidity & Temperature Web Server

Live environmental monitoring system built with **ESP32** and **DHT11**. The ESP32 connects to your local Wi-Fi (**Tech Vue**), reads humidity and temperature on **GPIO 22 (D22)**, and hosts an embedded asynchronous web backend displaying real-time data on any web browser.

---

## 📌 Features

- **Hardware Pin**: DHT11 Data connected to **GPIO 22 (D22)**.
- **Wi-Fi Pre-configured**:
  - **SSID**: `Tech Vue`
  - **Password**: `maxmus8085`
- **Embedded Web Backend**:
  - Serves an interactive dark-mode glassmorphic dashboard directly on port `80`.
  - Auto-refreshes live data asynchronously via `fetch('/api/data')` every 2 seconds (no full page reload).
  - Displays **Relative Humidity (%)**, **Temperature (°C / °F)**, and **Heat Index / Feels Like (°C)**.
  - Zero-dependency live **SVG history trend chart** (runs 100% offline without needing external CDNs).
  - Shows Wi-Fi Signal Strength (RSSI dBm), System Uptime, and Sensor Reading Health.
- **REST API Endpoint**:
  - `GET /api/data`: Returns live JSON telemetry for integrations, Home Assistant, Python, Node-RED, etc.
- **Auto-Reconnection Watchdog**: Automatically reconnects if Wi-Fi drops.
- **Python Companion Backend (`backend_server.py`)**: Included for testing the dashboard offline in simulation mode or proxying & logging data to CSV.

---

## 🔌 Hardware Wiring Diagram

```text
       ESP32 Dev Board                     DHT11 Sensor
   ┌──────────────────────┐             ┌─────────────────┐
   │                      │             │                 │
   │           3.3V / VIN ├─────────────┤ VCC (+)         │
   │                  GND ├─────────────┤ GND (-)         │
   │             GPIO 22  ├─────────────┤ DATA (Out)      │
   │               (D22)  │             │                 │
   └──────────────────────┘             └─────────────────┘
                                                │
                                    (Optional 4.7kΩ - 10kΩ 
                                     pull-up resistor to 3.3V 
                                     if using bare sensor)
```

> **Note on DHT11 modules**: Most 3-pin DHT11 sensor breakout modules already have an on-board SMD pull-up resistor. If you are using a bare 4-pin DHT11 component, connect a 10kΩ resistor between `VCC` and `DATA`.

---

## 🚀 Setup & Upload via Arduino IDE

### 1. Install ESP32 Board Package
1. Open **Arduino IDE**.
2. Go to **File** → **Preferences**.
3. In **Additional boards manager URLs**, add:
   ```text
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
4. Go to **Tools** → **Board** → **Boards Manager**, search for `esp32` by **Espressif Systems**, and click **Install**.

### 2. Install Required Libraries
In Arduino IDE, open **Tools** → **Manage Libraries...** and install:
1. **DHT sensor library** (by *Adafruit*) — click **Install All** to also include *Adafruit Unified Sensor*.

### 3. Upload Code
1. Open [`humidity.ino`](file:///F:/Vscc/esp32/humidity/humidity.ino).
2. Under **Tools**:
   - **Board**: Select `ESP32 Dev Module` (or your specific ESP32 board).
   - **Port**: Select your ESP32 COM port (e.g., `COM3`, `COM4`, etc.).
   - **Upload Speed**: `921600` (or `115200`).
3. Click the **Upload** arrow button (hold `BOOT` on the ESP32 if upload stays on `Connecting......`).

---

## 🌐 How to View Live in Your Web Browser

1. Open the **Serial Monitor** in Arduino IDE (`Ctrl + Shift + M`).
2. Set baud rate to **115200 baud**.
3. When the ESP32 starts, it connects to **Tech Vue** and outputs:
   ```text
   [WIFI] Connected successfully!
   [WIFI] IP Address : http://192.168.1.XXX
   [SERVER] HTTP Server started on port 80.
   ```
4. On any mobile phone, tablet, or PC connected to the **Tech Vue** Wi-Fi network:
   - Open your browser (Chrome, Edge, Safari, Firefox).
   - Navigate to:
     ```text
     http://192.168.1.XXX
     ```
   *(Replace with the actual IP address displayed in your Serial Monitor)*

---

## 📊 REST API Endpoint

Access `http://<ESP32_IP>/api/data` to receive raw JSON:
```json
{
  "status": "OK",
  "humidity": 64.0,
  "temperature": 28.2,
  "temperature_f": 82.8,
  "heat_index": 30.1,
  "rssi": -56,
  "uptime": 142,
  "uptime_str": "0h 2m 22s",
  "ip": "192.168.1.120",
  "reads": 71,
  "errors": 0
}
```

---

## 🐍 Optional: Python Companion Server / Simulator

You can test the web interface right now without the ESP32 board:
```powershell
cd "F:\Vscc\esp32\humidity"
python backend_server.py
```
Then visit **`http://localhost:5000`** in your browser.

To log telemetry from the live ESP32 into `sensor_log.csv`:
```powershell
python backend_server.py --esp32-ip 192.168.1.XXX
```

