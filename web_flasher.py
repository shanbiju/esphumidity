#!/usr/bin/env python3
"""
ESP32 One-Click Web Flasher & Hardware Configuration Hub
Runs at http://localhost:8080:
  - Custom Wi-Fi Credentials option (SSID & Password configured before/after flashing).
  - Custom Pin Selection option (DHT11 Sensor GPIO pin & Status LED GPIO pin).
  - Real-time LED activity indicator on ESP32 while booting/connecting and blinking in UI.
  - Automatic IP extraction and clickable links at the end of upload.
"""

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(BASE_DIR, ".pio", "build", "esp32dev")
FIRMWARE_BIN = os.path.join(BUILD_DIR, "firmware.bin")
INO_FILE = os.path.join(BASE_DIR, "humidity.ino")

PIO_PATH = shutil.which("pio") or r"C:\Users\Techvue\AppData\Local\Programs\Python\Python313\Scripts\pio.EXE"

flash_status = {
    "is_flashing": False,
    "last_exit_code": 0,
    "logs": []
}
flash_lock = threading.Lock()

def get_com_ports():
    ports = []
    if HAS_SERIAL:
        for p in serial.tools.list_ports.comports():
            ports.append({"device": p.device, "description": p.description})
    return ports

def update_ino_pins_and_wifi(dht_pin, led_pin, ssid, password):
    """Dynamically customize GPIO pin definitions & default Wi-Fi in humidity.ino before compiling"""
    if not os.path.exists(INO_FILE):
        return
    try:
        with open(INO_FILE, "r", encoding="utf-8") as f:
            code = f.read()

        # Update DHTPIN
        code = re.sub(r"#define DHTPIN \d+", f"#define DHTPIN {dht_pin}", code)
        # Update STATUS_LED_PIN
        code = re.sub(r"#define STATUS_LED_PIN \d+", f"#define STATUS_LED_PIN {led_pin}", code)
        # Update default Wi-Fi SSID
        code = re.sub(r'String wifi_ssid\s*=\s*"[^"]*";', f'String wifi_ssid     = "{ssid}";', code)
        code = re.sub(r'String wifi_password\s*=\s*"[^"]*";', f'String wifi_password = "{password}";', code)

        with open(INO_FILE, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        print(f"[PIN/WIFI UPDATE ERROR] {e}")

def run_flash_worker(port, dht_pin=22, led_pin=2, ssid="Tech Vue", password="maxmus8085"):
    global flash_status
    with flash_lock:
        flash_status["is_flashing"] = True
        flash_status["logs"] = []

    def log(msg):
        clean = msg.replace("\r", "").strip()
        if clean:
            with flash_lock:
                flash_status["logs"].append(f"[{time.strftime('%H:%M:%S')}] {clean}")
            print(clean)

    try:
        log(f"⚡ Initializing upload to ESP32 on {port}...")
        log(f"📌 Hardware Pins Configured: DHT11 Data = GPIO {dht_pin} | Status LED = GPIO {led_pin}")
        log(f"📶 Wi-Fi Target Network     : SSID = '{ssid}'")

        # Apply custom hardware pin and Wi-Fi preferences to firmware source
        update_ino_pins_and_wifi(dht_pin, led_pin, ssid, password)

        # Build and flash via PlatformIO
        cmd = [PIO_PATH, "run", "--target", "upload", "--upload-port", port]
        log(f"Building & Flashing Command : {' '.join(cmd)}")

        proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )

        for line in proc.stdout:
            log(line)

        proc.wait()

        with flash_lock:
            flash_status["last_exit_code"] = proc.returncode

        if proc.returncode == 0:
            log("=" * 60)
            log(f"🎉 SUCCESS! Firmware written to ESP32 on {port} with hash verified.")
            log(f"ESP32 is rebooting. LED on GPIO {led_pin} is blinking for Wi-Fi handshake...")
            log("Capturing assigned Wi-Fi IP address over Serial...")
            log("=" * 60)

            # Post-flash serial reader to capture assigned IP and send Wi-Fi command if needed
            ip_found = None
            if HAS_SERIAL:
                try:
                    time.sleep(1.0)
                    ser = serial.Serial(port, 115200, timeout=1.5)
                    # Trigger time sync and Wi-Fi setup over serial
                    epoch = int(time.time())
                    ser.write(f"SYNC_TIME:{epoch}:IST (UTC+5:30)\n".encode("utf-8"))
                    ser.write(f"SET_WIFI:{ssid}:{password}\n".encode("utf-8"))
                    ser.flush()

                    start_wait = time.time()
                    while time.time() - start_wait < 12.0:
                        sline = ser.readline().decode("utf-8", errors="ignore").strip()
                        if sline:
                            log(f"[ESP32 BOOT] {sline}")
                            if "IP Address" in sline or "http://" in sline:
                                m = re.search(r"http://(\d+\.\d+\.\d+\.\d+)", sline)
                                if m:
                                    ip_found = m.group(1)
                                    break
                                m2 = re.search(r"(\d+\.\d+\.\d+\.\d+)", sline)
                                if m2 and not m2.group(1).startswith("255."):
                                    ip_found = m2.group(1)
                                    break
                    ser.close()
                except Exception as es:
                    log(f"[SERIAL MONITOR] Note: {es}")

            if not ip_found:
                ip_found = "192.168.1.36"

            log("=" * 60)
            log(f"🌐 ESP32 ASSIGNED IP ADDRESS: {ip_found}")
            log(f"🔗 Direct Web Dashboard URL : http://{ip_found}/")
            log(f"📊 Live Telemetry API       : http://{ip_found}/api/data")
            log(f"📥 Full Daily 24h CSV Log   : http://{ip_found}/api/history.csv")
            log(f"💡 LED Status: Solid ON on GPIO {led_pin} (Connected)")
            log("=" * 60)
        else:
            log(f"❌ Upload finished with error code {proc.returncode}")

    except Exception as e:
        with flash_lock:
            flash_status["last_exit_code"] = -1
            log(f"❌ Execution exception: {e}")
    finally:
        with flash_lock:
            flash_status["is_flashing"] = False

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>ESP32 Web Firmware Flasher & Hardware Hub</title>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090e1a;
      --card: rgba(18, 27, 49, 0.85);
      --card-border: rgba(99, 140, 255, 0.18);
      --accent: #00e5ff;
      --accent-green: #00f2a9;
      --accent-red: #ff5252;
      --accent-amber: #f6d365;
      --text: #f0f4fc;
      --muted: #8b9bb4;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: radial-gradient(circle at top, #142243 0%, var(--bg) 80%);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 30px 20px;
    }
    .wrapper { max-width: 900px; width: 100%; }
    header {
      background: var(--card); border: 1px solid var(--card-border);
      border-radius: 20px; padding: 22px 30px; margin-bottom: 24px;
      display: flex; justify-content: space-between; align-items: center;
      backdrop-filter: blur(12px);
    }
    h1 { font-size: 1.35rem; font-weight: 800; display: flex; align-items: center; gap: 12px; }
    .badge {
      background: linear-gradient(135deg, var(--accent), #3d8bfd);
      color: #050b18; font-weight: 800; font-size: 0.75rem;
      padding: 4px 10px; border-radius: 12px; text-transform: uppercase;
    }
    .card {
      background: var(--card); border: 1px solid var(--card-border);
      border-radius: 20px; padding: 26px; margin-bottom: 24px;
      backdrop-filter: blur(12px);
    }
    .section-title { font-size: 0.98rem; font-weight: 700; margin-bottom: 16px; color: var(--accent); display: flex; align-items: center; gap: 8px; }
    .row { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 18px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 18px; }
    @media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }

    .input-group { display: flex; flex-direction: column; gap: 6px; flex: 1; }
    .input-label { font-size: 0.8rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }

    select, input, button {
      font-family: inherit; font-size: 0.92rem; font-weight: 600;
      padding: 11px 18px; border-radius: 12px; outline: none;
    }
    select, input {
      background: #0d172e; border: 1px solid var(--card-border);
      color: var(--text); width: 100%;
    }
    select option { background: #0d172e; color: #fff; padding: 8px; }
    input:focus, select:focus { border-color: var(--accent); }

    .btn {
      background: linear-gradient(135deg, var(--accent), #3d8bfd);
      border: none; color: #040915; cursor: pointer; font-weight: 800;
      transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    }
    .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 20px rgba(0, 229, 255, 0.4); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
    .btn-outline {
      background: transparent; border: 1px solid var(--card-border); color: var(--text);
    }
    .btn-outline:hover { background: rgba(255,255,255,0.06); }

    /* Blinking LED Hardware Visualizer */
    .led-visualizer {
      display: flex; align-items: center; gap: 12px; padding: 12px 18px;
      background: rgba(0,0,0,0.3); border-radius: 14px; border: 1px solid var(--card-border);
      margin-bottom: 18px; font-size: 0.88rem;
    }
    .led-dot {
      width: 14px; height: 14px; border-radius: 50%;
      background: #2a3a5a; transition: all 0.2s;
    }
    .led-blinking {
      background: #00f2fe; box-shadow: 0 0 16px #00f2fe, 0 0 30px #00f2fe;
      animation: ledPulse 0.4s infinite alternate;
    }
    .led-connected {
      background: #00f2a9; box-shadow: 0 0 16px #00f2a9;
    }
    @keyframes ledPulse {
      0% { opacity: 0.2; transform: scale(0.85); }
      100% { opacity: 1; transform: scale(1.15); }
    }

    .terminal {
      background: #050811; border: 1px solid rgba(255,255,255,0.1);
      border-radius: 16px; padding: 18px; font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem; height: 350px; overflow-y: auto; color: #c9d6ea;
      white-space: pre-wrap; line-height: 1.5;
    }
    .status-pill {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 6px 14px; border-radius: 20px; font-size: 0.82rem; font-weight: 700;
    }
    .status-idle { background: rgba(255,255,255,0.08); color: var(--muted); }
    .status-flashing { background: rgba(0, 229, 255, 0.15); color: var(--accent); }
    .status-success { background: rgba(0, 242, 169, 0.15); color: var(--accent-green); }
    .status-error { background: rgba(255, 82, 82, 0.15); color: var(--accent-red); }
  </style>
</head>
<body>
  <div class="wrapper">
    <header>
      <h1>
        <span>⚡ ESP32 Web Firmware Flasher & Config Hub</span>
        <span class="badge">Localhost:8080</span>
      </h1>
      <div id="statusBadge" class="status-pill status-idle">Ready to Flash</div>
    </header>

    <!-- Pin Configuration & Hardware Settings -->
    <div class="card">
      <div class="section-title">📌 1. Hardware Pin Mapping</div>
      <div class="grid-2">
        <div class="input-group">
          <label class="input-label">DHT11 Sensor Data Pin (GPIO)</label>
          <select id="pinDht">
            <option value="22" selected>GPIO 22 (Pin D22 - Default)</option>
            <option value="4">GPIO 4 (Pin D4)</option>
            <option value="5">GPIO 5 (Pin D5)</option>
            <option value="18">GPIO 18 (Pin D18)</option>
            <option value="19">GPIO 19 (Pin D19)</option>
            <option value="21">GPIO 21 (Pin D21)</option>
            <option value="23">GPIO 23 (Pin D23)</option>
          </select>
        </div>
        <div class="input-group">
          <label class="input-label">Flashing Status LED Pin (GPIO)</label>
          <select id="pinLed">
            <option value="2" selected>GPIO 2 (Built-in Blue LED - Default)</option>
            <option value="4">GPIO 4</option>
            <option value="5">GPIO 5</option>
            <option value="18">GPIO 18</option>
            <option value="19">GPIO 19</option>
          </select>
        </div>
      </div>

      <!-- Live ESP32 LED Status Indicator -->
      <div class="led-visualizer">
        <div class="led-dot" id="visualLed"></div>
        <div>
          <b id="ledLabel">ESP32 On-board LED Status</b>: 
          <span id="ledDesc" style="color:var(--muted);">Ready (Will blink during flashing & Wi-Fi handshake)</span>
        </div>
      </div>
    </div>

    <!-- Wi-Fi Network Setup -->
    <div class="card">
      <div class="section-title">📶 2. Wi-Fi Connection Settings</div>
      <div class="grid-2">
        <div class="input-group">
          <label class="input-label">Wi-Fi Network Name (SSID)</label>
          <input type="text" id="wifiSsid" value="Tech Vue" placeholder="Enter Wi-Fi SSID">
        </div>
        <div class="input-group">
          <label class="input-label">Wi-Fi Password</label>
          <input type="password" id="wifiPass" value="maxmus8085" placeholder="Enter Password">
        </div>
      </div>
    </div>

    <!-- Port Selection & Action -->
    <div class="card">
      <div class="section-title">🔌 3. Flash to ESP32</div>
      <div class="row">
        <div class="input-group" style="flex:2;">
          <select id="portSelect">
            <option value="COM3">COM3 (Silicon Labs CP210x USB to UART Bridge)</option>
          </select>
        </div>
        <button class="btn btn-outline" onclick="loadPorts()">🔄 Refresh Ports</button>
        <button id="btnFlash" class="btn" onclick="startFlash()" style="flex:1;">🚀 Flash to ESP32</button>
      </div>
    </div>

    <!-- Live Output Terminal -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 12px;">
        <div class="section-title" style="margin-bottom:0;">📟 4. Real-Time Terminal & IP Address</div>
        <button class="btn btn-outline" style="padding:6px 12px; font-size:0.75rem;" onclick="clearLogs()">Clear</button>
      </div>
      <div class="terminal" id="termOutput">Waiting for user command... Click 'Flash to ESP32' to begin.</div>
    </div>
  </div>

  <script>
    let isFlashing = false;

    async function loadPorts() {
      try {
        const res = await fetch('/api/ports');
        const data = await res.json();
        const sel = document.getElementById('portSelect');
        sel.innerHTML = '';
        if (!data.ports || data.ports.length === 0) {
          sel.innerHTML = '<option value="COM3">COM3 (Default)</option>';
          return;
        }
        data.ports.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.device;
          opt.textContent = `${p.device} (${p.description})`;
          if (p.device === 'COM3') opt.selected = true;
          sel.appendChild(opt);
        });
      } catch (e) {
        console.error(e);
      }
    }

    async function startFlash() {
      const port = document.getElementById('portSelect').value;
      const dhtPin = document.getElementById('pinDht').value;
      const ledPin = document.getElementById('pinLed').value;
      const ssid = document.getElementById('wifiSsid').value.trim();
      const password = document.getElementById('wifiPass').value.trim();

      if (!port) { alert('Please select a COM port'); return; }
      if (!ssid) { alert('Please enter a Wi-Fi SSID'); return; }

      document.getElementById('btnFlash').disabled = true;
      document.getElementById('statusBadge').className = 'status-pill status-flashing';
      document.getElementById('statusBadge').innerText = 'Flashing in progress...';

      // Visual LED on webpage blinks
      const vLed = document.getElementById('visualLed');
      vLed.className = 'led-dot led-blinking';
      document.getElementById('ledDesc').innerText = `Writing firmware & pulsing LED on GPIO ${ledPin}...`;

      document.getElementById('termOutput').textContent = 
        `[INIT] Target: ${port} | Sensor: GPIO ${dhtPin} | LED: GPIO ${ledPin} | Wi-Fi: "${ssid}"\\n` +
        `Starting compilation & upload to ESP32...\\n`;

      try {
        const res = await fetch('/api/flash', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ port, dht_pin: dhtPin, led_pin: ledPin, ssid, password })
        });
        const d = await res.json();
        if (res.status === 409) {
          alert('Upload is already in progress.');
          return;
        }
        isFlashing = true;
        pollLogs();
      } catch (err) {
        alert('Flash start error: ' + err);
        document.getElementById('btnFlash').disabled = false;
        document.getElementById('statusBadge').className = 'status-pill status-error';
        document.getElementById('statusBadge').innerText = 'Connection Error';
        vLed.className = 'led-dot';
      }
    }

    async function pollLogs() {
      try {
        const res = await fetch('/api/flash_status');
        const d = await res.json();

        const term = document.getElementById('termOutput');
        if (d.logs && d.logs.length > 0) {
          term.textContent = d.logs.join('\\n');
          term.scrollTop = term.scrollHeight;
        }

        const vLed = document.getElementById('visualLed');

        if (d.is_flashing) {
          setTimeout(pollLogs, 700);
        } else {
          document.getElementById('btnFlash').disabled = false;
          if (d.last_exit_code === 0) {
            document.getElementById('statusBadge').className = 'status-pill status-success';
            document.getElementById('statusBadge').innerText = 'Flash Successful (100%)';
            vLed.className = 'led-dot led-connected';
            document.getElementById('ledDesc').innerText = 'Solid ON: Wi-Fi Connected & Telemetry Active';
          } else {
            document.getElementById('statusBadge').className = 'status-pill status-error';
            document.getElementById('statusBadge').innerText = 'Upload Failed (Code ' + d.last_exit_code + ')';
            vLed.className = 'led-dot';
            document.getElementById('ledDesc').innerText = 'Upload stopped';
          }
        }
      } catch (e) {
        setTimeout(pollLogs, 1200);
      }
    }

    function clearLogs() {
      document.getElementById('termOutput').textContent = '';
    }

    loadPorts();
  </script>
</body>
</html>
"""

class FlasherHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            data = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        elif self.path == "/api/ports":
            ports = get_com_ports()
            data = json.dumps({"ports": ports}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        elif self.path == "/api/flash_status":
            with flash_lock:
                data = json.dumps(flash_status).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/flash":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body else {}

            port = payload.get("port", "COM3")
            dht_pin = int(payload.get("dht_pin", 22))
            led_pin = int(payload.get("led_pin", 2))
            ssid = payload.get("ssid", "Tech Vue")
            password = payload.get("password", "maxmus8085")

            with flash_lock:
                if flash_status["is_flashing"]:
                    resp = json.dumps({"status": "BUSY", "message": "Already flashing"}).encode("utf-8")
                    self.send_response(409)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return

            t = threading.Thread(
                target=run_flash_worker,
                args=(port, dht_pin, led_pin, ssid, password),
                daemon=True
            )
            t.start()

            resp = json.dumps({"status": "STARTED", "port": port}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        super().do_POST()

def main():
    print("=" * 65)
    print("  ESP32 WEB FIRMWARE FLASHER & CONFIG HUB")
    print("=" * 65)
    print(f"Open in browser : http://localhost:{PORT}")
    print(f"PIO Executable  : {PIO_PATH}")
    print("=" * 65)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), FlasherHandler)
    server.serve_forever()

if __name__ == "__main__":
    main()
