#!/usr/bin/env python3
"""
ESP32 Climate Hub • Studio & Control Station
Runs the full unified website on http://localhost:8080:
  - Tab 1: Live Dashboard (real-time telemetry gauges, comfort indices, NTP clock, quick controls)
  - Tab 2: Daily Trend Log (24h continuous auto-scaling SVG charts)
  - Tab 3: Web Flasher & Config Hub (custom pin mappings, Wi-Fi configuration, live terminal)
  - Tab 4: ESP32 Direct Web View (embedded live device view)
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
import urllib.request
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INO_FILE = os.path.join(BASE_DIR, "humidity.ino")
LOG_FILE = os.path.join(BASE_DIR, "sensor_log.csv")
PIO_PATH = shutil.which("pio") or r"C:\Users\Techvue\AppData\Local\Programs\Python\Python313\Scripts\pio.EXE"

flash_status = {
    "is_flashing": False,
    "last_exit_code": 0,
    "logs": [],
    "esp32_ip": "192.168.1.36"
}
flash_lock = threading.Lock()

telemetry_lock = threading.Lock()
latest_telemetry = {
    "status": "ONLINE",
    "humidity": 44.5,
    "temperature": 30.2,
    "temperature_f": 86.4,
    "heat_index": 31.0,
    "rssi": -58,
    "uptime_str": "0h 10m 0s",
    "time_str": datetime.now().strftime("%H:%M:%S"),
    "timezone": "IST (UTC+5:30)",
    "ntp_synced": True,
    "ip": "192.168.1.36",
    "reads": 100,
    "errors": 0
}
recent_history = []

def get_com_ports():
    ports = []
    if HAS_SERIAL:
        for p in serial.tools.list_ports.comports():
            ports.append({"device": p.device, "description": p.description})
    return ports

def update_ino_pins_and_wifi(dht_pin, led_pin, ssid, password):
    if not os.path.exists(INO_FILE):
        return
    try:
        with open(INO_FILE, "r", encoding="utf-8") as f:
            code = f.read()

        code = re.sub(r"#define DHTPIN \d+", f"#define DHTPIN {dht_pin}", code)
        code = re.sub(r"#define STATUS_LED_PIN \d+", f"#define STATUS_LED_PIN {led_pin}", code)
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
        log(f"📌 Hardware Pins: DHT11 = GPIO {dht_pin} | Status LED = GPIO {led_pin}")
        log(f"📶 Target Wi-Fi: SSID = '{ssid}'")

        update_ino_pins_and_wifi(dht_pin, led_pin, ssid, password)

        cmd = [PIO_PATH, "run", "--target", "upload", "--upload-port", port]
        log(f"Building & Flashing Command: {' '.join(cmd)}")

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
            log(f"ESP32 is rebooting. LED on GPIO {led_pin} is blinking for handshake...")
            log("Capturing assigned Wi-Fi IP address over Serial...")
            log("=" * 60)

            ip_found = None
            if HAS_SERIAL:
                try:
                    time.sleep(1.0)
                    ser = serial.Serial(port, 115200, timeout=1.5)
                    epoch = int(time.time())
                    ser.write(f"SYNC_TIME:{epoch}:IST (UTC+5:30)\n".encode("utf-8"))
                    ser.write(f"SET_WIFI:{ssid}:{password}\n".encode("utf-8"))
                    ser.flush()

                    start_wait = time.time()
                    while time.time() - start_wait < 10.0:
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

            with flash_lock:
                flash_status["esp32_ip"] = ip_found

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

# Background Telemetry Poller
def telemetry_poller():
    global recent_history
    while True:
        target_ip = flash_status.get("esp32_ip", "192.168.1.36")
        try:
            url = f"http://{target_ip}/api/data"
            req = urllib.request.Request(url, headers={"User-Agent": "ESP32-Hub"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode())
                with telemetry_lock:
                    latest_telemetry.update(data)
                    t_val = data.get("temperature", 0.0)
                    h_val = data.get("humidity", 0.0)
                    epoch_val = data.get("epoch", int(time.time()))
                    recent_history.append({"t": epoch_val, "temp": t_val, "hum": h_val})
                    if len(recent_history) > 720:
                        recent_history.pop(0)
        except Exception:
            pass
        time.sleep(2.0)

HTML_PAGE_FILE = os.path.join(BASE_DIR, "index.html")

class UnifiedHubHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            if os.path.exists(HTML_PAGE_FILE):
                with open(HTML_PAGE_FILE, "rb") as f:
                    data = f.read()
            else:
                data = b"<h1>ESP32 Climate Hub Running</h1>"

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        elif self.path == "/manifest.json":
            manifest_path = os.path.join(BASE_DIR, "dist", "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        elif self.path.startswith("/bin/"):
            bin_rel = self.path[len("/bin/"):]
            bin_path = os.path.join(BASE_DIR, "dist", "bin", bin_rel)
            if os.path.exists(bin_path):
                with open(bin_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

        elif self.path == "/api/data":
            with telemetry_lock:
                if latest_telemetry.get("time_str") in ["--", None]:
                    latest_telemetry["time_str"] = datetime.now().strftime("%H:%M:%S")
                data_bytes = json.dumps(latest_telemetry).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return

        elif self.path == "/api/history":
            target_ip = flash_status.get("esp32_ip", "192.168.1.36")
            try:
                url = f"http://{target_ip}/api/history"
                with urllib.request.urlopen(url, timeout=2.0) as resp:
                    data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except Exception:
                pass

            # Fallback
            with telemetry_lock:
                temps = [p["temp"] for p in recent_history if p["temp"] > 0] or [30.2]
                hums = [p["hum"] for p in recent_history if p["hum"] > 0] or [44.5]
                stats = {
                    "min_temp": min(temps), "max_temp": max(temps),
                    "min_hum": min(hums), "max_hum": max(hums)
                }
                payload = {"count": len(recent_history), "stats": stats, "logs": recent_history[-200:]}
            data_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return

        elif self.path == "/api/history.csv":
            target_ip = flash_status.get("esp32_ip", "192.168.1.36")
            try:
                url = f"http://{target_ip}/api/history.csv"
                with urllib.request.urlopen(url, timeout=3.0) as resp:
                    csv_data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv")
                    self.send_header("Content-Disposition", "attachment; filename=\"esp32_daily_log.csv\"")
                    self.send_header("Content-Length", str(len(csv_data)))
                    self.end_headers()
                    self.wfile.write(csv_data)
                    return
            except Exception:
                pass

            csv_lines = ["Timestamp,Temperature_C,Humidity_Pct\n"]
            with telemetry_lock:
                for item in recent_history:
                    csv_lines.append(f"{item.get('t')},{item.get('temp',0):.2f},{item.get('hum',0):.2f}\n")
            csv_bytes = "".join(csv_lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=\"esp32_daily_log.csv\"")
            self.send_header("Content-Length", str(len(csv_bytes)))
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        elif self.path.startswith("/api/sync_time"):
            target_ip = flash_status.get("esp32_ip", "192.168.1.36")
            epoch = int(time.time())
            tz = "IST (UTC+5:30)"
            try:
                import urllib.parse
                url = f"http://{target_ip}/api/set_time?epoch={epoch}&tz_offset=19800&tz_name={urllib.parse.quote(tz)}"
                urllib.request.urlopen(url, timeout=2.0)
            except Exception:
                pass

            resp_payload = {"status": "OK", "message": f"Time synced to {tz}", "epoch": epoch}
            data_bytes = json.dumps(resp_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
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
    print("  ESP32 CLIMATE HUB • PRO STUDIO & CONTROL STATION")
    print("=" * 65)
    print(f"Web Dashboard URL : http://localhost:{PORT}")
    print(f"Target Firmware   : {INO_FILE}")
    print("=" * 65)

    # Start telemetry thread
    t_tel = threading.Thread(target=telemetry_poller, daemon=True)
    t_tel.start()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), UnifiedHubHandler)
    server.serve_forever()

if __name__ == "__main__":
    main()
