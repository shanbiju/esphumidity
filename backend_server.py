#!/usr/bin/env python3
"""
ESP32 + DHT11 Companion Backend & Web Dashboard with COM Port Connectivity
Features:
  - Direct Serial COM Port connection (COM3, etc.) or Wi-Fi IP bridge.
  - Automatic PC clock and Timezone synchronization to ESP32 over COM port.
  - Full daily 24-hour sensor logging with CSV export and historical telemetry.
  - Web dashboard accessible at http://localhost:5000 or on local network.
  - Zero external requirements: runs directly on standard Python 3 + pyserial.

Usage:
  python backend_server.py --com-port COM3
  python backend_server.py --esp32-ip 192.168.1.36
  python backend_server.py (defaults to auto-detecting COM3 or Wi-Fi 192.168.1.36)
"""

import argparse
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

PORT = 5000
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_log.csv")

# Global state
telemetry_lock = threading.Lock()
latest_telemetry = {
    "status": "CONNECTING",
    "humidity": 0.0,
    "temperature": 0.0,
    "temperature_f": 32.0,
    "heat_index": 0.0,
    "rssi": 0,
    "uptime_str": "--",
    "time_str": "--",
    "timezone": "IST (UTC+5:30)",
    "ntp_synced": False,
    "source": "None",
    "port": "None",
    "last_sync": "Never",
    "reads": 0,
    "errors": 0
}

recent_history = []  # Ring buffer of recent points for graph {time, temp, hum}
serial_connection = None
target_com_port = None
target_esp_ip = None

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def detect_default_com_port():
    if not HAS_SERIAL:
        return None
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if "CP210" in p.description or "CH340" in p.description or "UART" in p.description or "COM3" in p.device:
            return p.device
    if ports:
        return ports[0].device
    return None

def log_reading(temp_c, hum, status, source):
    header_needed = not os.path.exists(LOG_FILE)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("timestamp,source,temperature_c,humidity_pct,status\n")
            f.write(f"{datetime.now().isoformat()},{source},{temp_c:.2f},{hum:.2f},{status}\n")
    except Exception as e:
        print(f"[LOG ERROR] {e}")

def sync_time_over_serial(ser, tz_name="IST (UTC+5:30)"):
    try:
        epoch = int(time.time())
        cmd = f"SYNC_TIME:{epoch}:{tz_name}\n"
        ser.write(cmd.encode("utf-8"))
        ser.flush()
        with telemetry_lock:
            latest_telemetry["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[SERIAL] Synchronized time to ESP32: Epoch {epoch} ({tz_name})")
        return True
    except Exception as e:
        print(f"[SERIAL SYNC ERROR] {e}")
        return False

def serial_worker(port_name, baud=115200):
    global serial_connection
    print(f"[SERIAL] Starting background thread for {port_name} @ {baud} baud...")
    while True:
        try:
            print(f"[SERIAL] Opening {port_name}...")
            ser = serial.Serial(port_name, baud, timeout=2.0)
            serial_connection = ser
            time.sleep(2.0)  # Wait for ESP32 boot/reset
            
            # Synchronize local PC time immediately
            sync_time_over_serial(ser)

            with telemetry_lock:
                latest_telemetry["status"] = "CONNECTED"
                latest_telemetry["port"] = port_name
                latest_telemetry["source"] = "COM Port"

            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                
                # Check for structured JSON or formatted logs
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        with telemetry_lock:
                            latest_telemetry.update(data)
                            latest_telemetry["source"] = f"COM Port ({port_name})"
                            latest_telemetry["reads"] = latest_telemetry.get("reads", 0) + 1
                            
                            t = data.get("temperature", 0.0)
                            h = data.get("humidity", 0.0)
                            cur_time = data.get("time_str", datetime.now().strftime("%H:%M:%S"))
                            recent_history.append({"time": cur_time, "temp": t, "hum": h})
                            if len(recent_history) > 300:
                                recent_history.pop(0)
                        
                        log_reading(data.get("temperature", 0.0), data.get("humidity", 0.0), "OK", f"COM:{port_name}")
                    except Exception as ej:
                        pass
                elif "Humidity:" in line or "TEMP:" in line:
                    print(f"[{port_name}] {line}")
        except Exception as e:
            print(f"[SERIAL ERROR on {port_name}]: {e}. Retrying in 5 seconds...")
            with telemetry_lock:
                latest_telemetry["status"] = "DISCONNECTED"
            time.sleep(5)

def wifi_poll_worker(esp_ip):
    print(f"[WIFI POLL] Polling ESP32 at http://{esp_ip}...")
    while True:
        try:
            url = f"http://{esp_ip}/api/data"
            req = urllib.request.Request(url, headers={"User-Agent": "ESP32-Companion-Backend"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with telemetry_lock:
                    latest_telemetry.update(data)
                    latest_telemetry["source"] = f"Wi-Fi (http://{esp_ip})"
                    cur_time = data.get("time_str", datetime.now().strftime("%H:%M:%S"))
                    recent_history.append({"time": cur_time, "temp": data.get("temperature", 0), "hum": data.get("humidity", 0)})
                    if len(recent_history) > 300:
                        recent_history.pop(0)
                log_reading(data.get("temperature", 0.0), data.get("humidity", 0.0), "OK", f"WiFi:{esp_ip}")
        except Exception as e:
            pass
        time.sleep(2.5)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ESP32 Humidity & Climate Companion Station</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-dark: #080d19;
      --card-bg: rgba(16, 25, 46, 0.75);
      --card-border: rgba(99, 140, 255, 0.16);
      --card-hover: rgba(99, 140, 255, 0.28);
      --accent-cyan: #00e5ff;
      --accent-blue: #3d8bfd;
      --accent-green: #00f2a9;
      --accent-orange: #ff9f43;
      --accent-purple: #9d4edd;
      --text-main: #f0f4fc;
      --text-muted: #8b9bb4;
      --text-dim: #5c6c84;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: radial-gradient(circle at 10% 20%, #0d172e 0%, #080d19 80%);
      color: var(--text-main);
      font-family: 'Plus Jakarta Sans', sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      padding: 24px;
    }
    .container { max-width: 1200px; margin: 0 auto; width: 100%; }
    header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 18px 26px; background: var(--card-bg);
      border: 1px solid var(--card-border); border-radius: 20px;
      backdrop-filter: blur(14px); margin-bottom: 24px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.4);
    }
    .brand-title { font-size: 1.45rem; font-weight: 800; display: flex; align-items: center; gap: 12px; }
    .brand-badge {
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
      color: #030816; font-size: 0.75rem; font-weight: 800;
      padding: 4px 10px; border-radius: 20px; text-transform: uppercase;
    }
    .header-pills { display: flex; align-items: center; gap: 12px; }
    .pill {
      background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
      padding: 6px 14px; border-radius: 20px; font-size: 0.82rem; font-family: 'JetBrains Mono', monospace;
      display: flex; align-items: center; gap: 8px;
    }
    .dot-live { width: 9px; height: 9px; border-radius: 50%; background: var(--accent-green); box-shadow: 0 0 10px var(--accent-green); animation: pulse 1.8s infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }

    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
    @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
    
    .card {
      background: var(--card-bg); border: 1px solid var(--card-border);
      border-radius: 20px; padding: 26px; backdrop-filter: blur(14px);
      box-shadow: 0 10px 30px rgba(0,0,0,0.3); transition: border-color 0.3s;
    }
    .card:hover { border-color: var(--card-hover); }

    .val-display { display: flex; align-items: baseline; gap: 10px; margin: 12px 0; }
    .val-num { font-size: 4rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; line-height: 1; }
    .val-unit { font-size: 1.8rem; font-weight: 600; color: var(--text-muted); }
    .val-sub { font-size: 0.95rem; color: var(--text-muted); display: flex; justify-content: space-between; }

    .chart-container { margin-top: 16px; height: 180px; position: relative; }
    svg.sparkline { width: 100%; height: 100%; overflow: visible; }

    .stats-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
    @media (max-width: 900px) { .stats-strip { grid-template-columns: 1fr 1fr; } }
    .stat-box {
      background: var(--card-bg); border: 1px solid var(--card-border);
      padding: 16px 20px; border-radius: 16px; backdrop-filter: blur(14px);
    }
    .stat-label { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 6px; }
    .stat-val { font-size: 1.35rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

    .control-bar {
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;
      padding: 20px 26px; background: var(--card-bg); border: 1px solid var(--card-border);
      border-radius: 20px; margin-bottom: 24px;
    }
    .btn {
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
      border: none; color: #040814; font-weight: 700; font-size: 0.88rem;
      padding: 10px 20px; border-radius: 12px; cursor: pointer; transition: transform 0.15s, box-shadow 0.2s;
    }
    .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,229,255,0.4); }
    .btn-outline {
      background: transparent; border: 1px solid var(--card-border); color: var(--text-main);
    }
    .btn-outline:hover { background: rgba(255,255,255,0.06); border-color: var(--accent-cyan); color: var(--accent-cyan); }

    select, input {
      background: rgba(16, 25, 46, 0.95); border: 1px solid var(--card-border);
      color: var(--text-main); padding: 9px 16px; border-radius: 10px;
      font-size: 0.9rem; font-family: inherit; outline: none;
    }
    select option {
      background: #0d172e;
      color: #f0f4fc;
      padding: 8px;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand-title">
        <span>💧 ESP32 Climate Hub</span>
        <span class="brand-badge" id="source-badge">CONNECTING...</span>
      </div>
      <div class="header-pills">
        <div class="pill"><span class="dot-live"></span> <span id="status-text">ONLINE</span></div>
        <div class="pill" id="clock-pill">🕒 --:--:--</div>
        <div class="pill" id="tz-pill">🌐 IST (UTC+5:30)</div>
      </div>
    </header>

    <div class="grid-2">
      <!-- Humidity Card -->
      <div class="card" style="border-top: 4px solid var(--accent-cyan);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h3 style="color:var(--accent-cyan); font-weight:700;">RELATIVE HUMIDITY</h3>
          <span style="font-size:0.8rem; background:rgba(0,229,255,0.12); color:var(--accent-cyan); padding:4px 10px; border-radius:12px;" id="hum-comfort">COMFORTABLE</span>
        </div>
        <div class="val-display">
          <span class="val-num" id="val-hum" style="color:var(--accent-cyan);">--</span>
          <span class="val-unit">%</span>
        </div>
        <div class="val-sub">
          <span>Min: <b id="min-hum">--%</b></span>
          <span>Max: <b id="max-hum">--%</b></span>
          <span>Target: <b>40 - 60%</b></span>
        </div>
        <div class="chart-container">
          <svg class="sparkline" id="hum-chart" viewBox="0 0 500 150" preserveAspectRatio="none"></svg>
        </div>
      </div>

      <!-- Temperature Card -->
      <div class="card" style="border-top: 4px solid var(--accent-orange);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h3 style="color:var(--accent-orange); font-weight:700;">AMBIENT TEMPERATURE</h3>
          <span style="font-size:0.8rem; background:rgba(255,159,67,0.15); color:var(--accent-orange); padding:4px 10px; border-radius:12px;" id="hi-display">HEAT INDEX: --°C</span>
        </div>
        <div class="val-display">
          <span class="val-num" id="val-temp" style="color:var(--accent-orange);">--</span>
          <span class="val-unit">°C</span>
          <span style="font-size:1.5rem; color:var(--text-muted); margin-left:14px;" id="val-temp-f">(--°F)</span>
        </div>
        <div class="val-sub">
          <span>Min: <b id="min-temp">--°C</b></span>
          <span>Max: <b id="max-temp">--°C</b></span>
          <span>Sensor: <b>DHT11 (GPIO 22)</b></span>
        </div>
        <div class="chart-container">
          <svg class="sparkline" id="temp-chart" viewBox="0 0 500 150" preserveAspectRatio="none"></svg>
        </div>
      </div>
    </div>

    <div class="stats-strip">
      <div class="stat-box">
        <div class="stat-label">Active Connection</div>
        <div class="stat-val" style="color:var(--accent-cyan);" id="stat-conn">COM3</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">ESP32 Uptime</div>
        <div class="stat-val" id="stat-uptime">0h 0m 0s</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Readings Taken</div>
        <div class="stat-val" style="color:var(--accent-green);" id="stat-reads">0</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Last Clock Sync</div>
        <div class="stat-val" style="font-size:0.95rem; color:var(--text-muted);" id="stat-sync">Auto (NTP/PC)</div>
      </div>
    </div>

    <div class="control-bar">
      <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
        <label style="font-weight:600; font-size:0.88rem;">Timezone Selection:</label>
        <select id="tz-select" onchange="updateTimezone()">
          <option value="IST (UTC+5:30)" selected>India Standard Time (IST UTC+5:30)</option>
          <option value="UTC (UTC+0:00)">Coordinated Universal Time (UTC+0)</option>
          <option value="EST (UTC-5:00)">Eastern Standard Time (EST UTC-5)</option>
          <option value="CST (UTC-6:00)">Central Standard Time (CST UTC-6)</option>
          <option value="PST (UTC-8:00)">Pacific Standard Time (PST UTC-8)</option>
          <option value="CET (UTC+1:00)">Central European Time (CET UTC+1)</option>
          <option value="GST (UTC+4:00)">Gulf Standard Time (GST UTC+4)</option>
          <option value="SGT (UTC+8:00)">Singapore / HK Time (SGT UTC+8)</option>
          <option value="JST (UTC+9:00)">Japan Standard Time (JST UTC+9)</option>
          <option value="AEST (UTC+10:00)">Australian Eastern (AEST UTC+10)</option>
        </select>
        <button class="btn" onclick="syncPcTime()">⚡ Sync PC Time to ESP32</button>
      </div>

      <div style="display:flex; gap:10px;">
        <a href="/api/history.csv" download="esp32_humidity_full_log.csv" class="btn btn-outline" style="text-decoration:none;">📥 Export 24h CSV Log</a>
        <button class="btn btn-outline" onclick="fetchHistory()">🔄 Refresh Stats</button>
      </div>
    </div>
  </div>

  <script>
    let humHistory = [];
    let tempHistory = [];

    function renderSparkline(svgId, data, color) {
      const svg = document.getElementById(svgId);
      if (!svg || data.length < 2) return;
      const w = 500, h = 150;
      const min = Math.min(...data) - 0.5;
      const max = Math.max(...data) + 0.5;
      const range = (max - min) || 1;

      const pts = data.map((d, i) => {
        const x = (i / (data.length - 1)) * w;
        const y = h - ((d - min) / range) * (h - 20) - 10;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');

      const fillPts = `0,${h} ${pts} ${w},${h}`;
      svg.innerHTML = `
        <defs>
          <linearGradient id="grad-${svgId}" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="${color}" stop-opacity="0.35"/>
            <stop offset="100%" stop-color="${color}" stop-opacity="0.0"/>
          </linearGradient>
        </defs>
        <polygon points="${fillPts}" fill="url(#grad-${svgId})"/>
        <polyline fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" points="${pts}"/>
      `;
    }

    async function updateData() {
      try {
        const res = await fetch('/api/data');
        const d = await res.json();
        
        document.getElementById('source-badge').innerText = d.source || 'ONLINE';
        document.getElementById('stat-conn').innerText = d.port || d.ip || 'COM3';
        document.getElementById('status-text').innerText = d.status || 'OK';
        
        if (d.humidity !== undefined && d.humidity > 0) {
          document.getElementById('val-hum').innerText = Number(d.humidity).toFixed(1);
          humHistory.push(Number(d.humidity));
          if (humHistory.length > 50) humHistory.shift();
          renderSparkline('hum-chart', humHistory, '#00e5ff');
          
          const comfort = document.getElementById('hum-comfort');
          if (d.humidity < 30) { comfort.innerText = 'TOO DRY'; comfort.style.color = '#ff9f43'; }
          else if (d.humidity > 70) { comfort.innerText = 'HIGH HUMIDITY'; comfort.style.color = '#ff6b6b'; }
          else { comfort.innerText = 'COMFORTABLE'; comfort.style.color = '#00f2a9'; }
        }

        if (d.temperature !== undefined && d.temperature > 0) {
          document.getElementById('val-temp').innerText = Number(d.temperature).toFixed(1);
          document.getElementById('val-temp-f').innerText = `(${Number(d.temperature_f).toFixed(1)}°F)`;
          tempHistory.push(Number(d.temperature));
          if (tempHistory.length > 50) tempHistory.shift();
          renderSparkline('temp-chart', tempHistory, '#ff9f43');
        }

        if (d.heat_index) document.getElementById('hi-display').innerText = `HEAT INDEX: ${Number(d.heat_index).toFixed(1)}°C`;
        if (d.uptime_str) document.getElementById('stat-uptime').innerText = d.uptime_str;
        if (d.reads) document.getElementById('stat-reads').innerText = d.reads;
        if (d.time_str) document.getElementById('clock-pill').innerText = `🕒 ${d.time_str}`;
        if (d.timezone) {
          document.getElementById('tz-pill').innerText = `🌐 ${d.timezone}`;
        }
        if (d.last_sync) document.getElementById('stat-sync').innerText = d.last_sync;

      } catch (e) {
        console.error(e);
      }
    }

    async function fetchHistory() {
      try {
        const res = await fetch('/api/history');
        const d = await res.json();
        if (d.stats) {
          document.getElementById('min-temp').innerText = `${d.stats.min_temp.toFixed(1)}°C`;
          document.getElementById('max-temp').innerText = `${d.stats.max_temp.toFixed(1)}°C`;
          document.getElementById('min-hum').innerText = `${d.stats.min_hum.toFixed(1)}%`;
          document.getElementById('max-hum').innerText = `${d.stats.max_hum.toFixed(1)}%`;
        }
      } catch (e) { console.error(e); }
    }

    async function syncPcTime() {
      const epoch = Math.floor(Date.now() / 1000);
      const tz = document.getElementById('tz-select').value;
      try {
        const res = await fetch(`/api/sync_time?epoch=${epoch}&timezone=${encodeURIComponent(tz)}`);
        const r = await res.json();
        alert(r.message || 'PC Time Synchronized Successfully!');
        updateData();
      } catch (e) {
        alert('Sync error: ' + e);
      }
    }

    function updateTimezone() {
      syncPcTime();
    }

    setInterval(updateData, 2000);
    setInterval(fetchHistory, 10000);
    updateData();
    fetchHistory();
  </script>
</body>
</html>
"""

class SensorCompanionHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        global serial_connection

        if self.path == "/" or self.path == "/index.html":
            data_bytes = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return

        elif self.path == "/api/data":
            # If ESP32 IP is active and COM is not, query Wi-Fi
            if target_esp_ip and not serial_connection:
                try:
                    url = f"http://{target_esp_ip}/api/data"
                    req = urllib.request.Request(url, headers={"User-Agent": "ESP32-Companion"})
                    with urllib.request.urlopen(req, timeout=2.0) as resp:
                        d = json.loads(resp.read().decode())
                        with telemetry_lock:
                            latest_telemetry.update(d)
                except Exception:
                    pass

            with telemetry_lock:
                # Add clock fallback if not yet set
                if latest_telemetry.get("time_str") in ["--", "00:00:00", None]:
                    latest_telemetry["time_str"] = datetime.now().strftime("%H:%M:%S")
                data_bytes = json.dumps(latest_telemetry).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return

        elif self.path == "/api/history":
            # Query ESP32 or compute from recent history
            stats = {
                "min_temp": 28.0, "max_temp": 32.0,
                "min_hum": 45.0, "max_hum": 60.0
            }
            if target_esp_ip:
                try:
                    url = f"http://{target_esp_ip}/api/history"
                    with urllib.request.urlopen(url, timeout=2.0) as resp:
                        d = json.loads(resp.read().decode())
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        out = json.dumps(d).encode("utf-8")
                        self.send_header("Content-Length", str(len(out)))
                        self.end_headers()
                        self.wfile.write(out)
                        return
                except Exception:
                    pass

            with telemetry_lock:
                temps = [p["temp"] for p in recent_history if p["temp"] > 0]
                hums = [p["hum"] for p in recent_history if p["hum"] > 0]
                if temps:
                    stats["min_temp"] = min(temps)
                    stats["max_temp"] = max(temps)
                if hums:
                    stats["min_hum"] = min(hums)
                    stats["max_hum"] = max(hums)
                payload = {
                    "count": len(recent_history),
                    "stats": stats,
                    "logs": recent_history[-50:]
                }
            data_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return

        elif self.path == "/api/history.csv":
            if target_esp_ip:
                try:
                    url = f"http://{target_esp_ip}/api/history.csv"
                    with urllib.request.urlopen(url, timeout=3.0) as resp:
                        csv_data = resp.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/csv")
                        self.send_header("Content-Disposition", "attachment; filename=\"esp32_humidity_full_log.csv\"")
                        self.send_header("Content-Length", str(len(csv_data)))
                        self.end_headers()
                        self.wfile.write(csv_data)
                        return
                except Exception:
                    pass

            # Fallback to local csv
            csv_lines = ["Index,Time,Temperature_C,Humidity_Pct\n"]
            with telemetry_lock:
                for idx, item in enumerate(recent_history):
                    csv_lines.append(f"{idx+1},{item.get('time','--')},{item.get('temp',0):.2f},{item.get('hum',0):.2f}\n")
            csv_bytes = "".join(csv_lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=\"esp32_humidity_full_log.csv\"")
            self.send_header("Content-Length", str(len(csv_bytes)))
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        elif self.path.startswith("/api/sync_time"):
            # Parse params
            epoch = int(time.time())
            tz = "IST (UTC+5:30)"
            if "?" in self.path:
                parts = self.path.split("?")[1].split("&")
                for p in parts:
                    if p.startswith("epoch="):
                        try: epoch = int(p.split("=")[1])
                        except: pass
                    elif p.startswith("timezone="):
                        try:
                            import urllib.parse
                            tz = urllib.parse.unquote(p.split("=")[1])
                        except: pass

            res_msg = "Time sync initiated."
            if serial_connection:
                sync_time_over_serial(serial_connection, tz)
                res_msg = f"Synced over COM port to {tz}"
            elif target_esp_ip:
                try:
                    import urllib.parse
                    url = f"http://{target_esp_ip}/api/set_time?epoch={epoch}&tz_offset=19800&tz_name={urllib.parse.quote(tz)}"
                    urllib.request.urlopen(url, timeout=2.0)
                    res_msg = f"Synced over Wi-Fi HTTP to {tz}"
                except Exception as e:
                    res_msg = f"Sync over Wi-Fi error: {e}"

            with telemetry_lock:
                latest_telemetry["timezone"] = tz
                latest_telemetry["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            resp_payload = {"status": "OK", "message": res_msg, "epoch": epoch, "timezone": tz}
            data_bytes = json.dumps(resp_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return

        super().do_GET()


def main():
    global target_com_port, target_esp_ip
    parser = argparse.ArgumentParser(description="ESP32 Humidity & Climate Companion Station")
    parser.add_argument("--com-port", help="Serial COM port (e.g. COM3)", default=None)
    parser.add_argument("--esp32-ip", help="ESP32 IP address (e.g. 192.168.1.36)", default=None)
    parser.add_argument("--port", type=int, default=PORT, help=f"HTTP Server port (default {PORT})")
    args = parser.parse_args()

    # Determine port or IP
    if args.com_port:
        target_com_port = args.com_port
    else:
        target_com_port = detect_default_com_port()

    if args.esp32_ip:
        target_esp_ip = args.esp32_ip
    else:
        # Default known IP
        target_esp_ip = "192.168.1.36"

    local_ip = get_local_ip()

    print("=" * 65)
    print("  ESP32 HUMIDITY & CLIMATE STATION - COMPANION BACKEND")
    print("=" * 65)
    print(f"Web Dashboard URL   : http://localhost:{args.port}")
    print(f"Network Dashboard   : http://{local_ip}:{args.port}")
    print(f"ESP32 Wi-Fi Address : http://{target_esp_ip}")
    print(f"ESP32 COM Port      : {target_com_port or 'Not detected'}")
    print(f"CSV Storage Path    : {LOG_FILE}")
    print("=" * 65)

    # Start background workers
    if target_com_port and HAS_SERIAL:
        t_ser = threading.Thread(target=serial_worker, args=(target_com_port, 115200), daemon=True)
        t_ser.start()

    if target_esp_ip:
        t_wifi = threading.Thread(target=wifi_poll_worker, args=(target_esp_ip,), daemon=True)
        t_wifi.start()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), SensorCompanionHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")

if __name__ == "__main__":
    main()
