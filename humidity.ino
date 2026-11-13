/*
 * =========================================================================================
 * Project: ESP32 + DHT11 Live Humidity & Temperature Web Server + Full Daily Logger
 * Target: ESP32 Dev Module (COM3)
 * Sensor: DHT11 on GPIO 22 (D22)
 * Wi-Fi: "Tech Vue" / "maxmus8085"
 * Features:
 *   - Automatic Internet NTP Time Sync with Configurable Timezone (Default: IST UTC+5:30)
 *   - Auto Sync Time via Serial/COM Port (`SYNC_TIME:<epoch>:<timezone_str>`)
 *   - Dual Mode: Serves directly on Wi-Fi IP (port 80) AND streams JSON over COM/Serial (115200)
 *   - Built-in Ring Buffer for 24-Hour / Full Day Log with Min/Max/Avg Analytics
 *   - REST API endpoints:
 *       /api/data        -> Live telemetry, sensor status, NTP sync status, timezone
 *       /api/history     -> 24-hour log points with timestamps
 *       /api/history.csv -> Full CSV download directly from ESP32
 *       /api/set_time    -> Manual / browser epoch time sync
 *   - Live interactive Web Dashboard with:
 *       * Real-time gauges, live graphs, daily stats (Min/Max/Avg)
 *       * Timezone selector & Internet NTP status badge
 *       * CSV Log Export button
 * =========================================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>
#include <time.h>
#include <Preferences.h>

// -----------------------------------------------------------------------------
// Hardware & Pin Configuration
// -----------------------------------------------------------------------------
int dhtPin = 22;           // Default GPIO 22 (configurable via NVS & Serial)
#define DHTTYPE DHT11

// On-board Activity & Status LED (GPIO 2 on ESP32 DevKit)
int statusLedPin = 2;      // Default GPIO 2 (configurable via NVS & Serial)

Preferences prefs;
DHT dht(dhtPin, DHTTYPE);
WebServer server(80);

// Wi-Fi Credentials (configurable via Web Flasher / NVS)
String wifi_ssid     = "Tech Vue";
String wifi_password = "maxmus8085";

// -----------------------------------------------------------------------------
// Time & Timezone Configuration (Internet NTP + Local Sync)
// -----------------------------------------------------------------------------
// Default to India Standard Time (IST): UTC +5:30 = 19800 seconds
long gmtOffset_sec     = 19800;
int  daylightOffset_sec = 0;
char timezoneName[32]  = "IST (UTC+5:30)";
const char* ntpServer1 = "pool.ntp.org";
const char* ntpServer2 = "time.nist.gov";
const char* ntpServer3 = "time.google.com";

bool ntpSynced = false;
unsigned long lastNtpAttempt = 0;

// -----------------------------------------------------------------------------
// Sensor State
// -----------------------------------------------------------------------------
float currentTempC     = 0.0;
float currentTempF     = 0.0;
float currentHumidity  = 0.0;
float currentHeatIndex = 0.0;
bool  sensorReadOk     = false;

unsigned long lastSensorReadTime = 0;
const unsigned long SENSOR_READ_INTERVAL_MS = 2000;

unsigned long totalReadCount   = 0;
unsigned long sensorErrorCount = 0;
unsigned long bootTimeMs       = 0;

// -----------------------------------------------------------------------------
// 24-Hour Daily Log Storage (Periodic snapshots every 60s -> 1440 points for 24h)
// -----------------------------------------------------------------------------
struct LogEntry {
  uint32_t timestamp; // Unix epoch timestamp
  float temp;         // Celsius
  float hum;          // Humidity %
};

const int MAX_LOG_ENTRIES = 720; // 720 entries @ 2-min intervals = 24 full hours
LogEntry dailyLogs[MAX_LOG_ENTRIES];
int logCount = 0;
int logHead  = 0; // Ring buffer write index
unsigned long lastLogTime = 0;
const unsigned long LOG_INTERVAL_MS = 120000; // Log point every 2 minutes

// Analytics
float minTempToday = 999.0;
float maxTempToday = -999.0;
float minHumToday  = 999.0;
float maxHumToday  = -999.0;

// Forward declarations
void handleRoot();
void handleApiData();
void handleApiHistory();
void handleApiHistoryCsv();
void handleSetTime();
void handleNotFound();
void readDhtSensor();
void syncNtpTime();
void checkSerialCommands();
String getFormattedTime(time_t raw);
String getUptimeFormatted();

// -----------------------------------------------------------------------------
// Embedded Modern Web Dashboard (HTML5, SVG Live Charts, Glassmorphism)
// -----------------------------------------------------------------------------
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP32 • Live Climate & Daily Logger</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #060913;
            --bg-card: rgba(15, 23, 42, 0.78);
            --bg-card-hover: rgba(22, 34, 62, 0.9);
            --border-glass: rgba(255, 255, 255, 0.08);
            --accent-cyan: #00e5ff;
            --accent-blue: #3d8bfd;
            --accent-amber: #ffb300;
            --accent-emerald: #00f2a9;
            --accent-rose: #ff5252;
            --accent-purple: #9d4edd;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --text-dim: #64748b;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background: radial-gradient(circle at 50% 0%, #101d3b 0%, var(--bg-base) 80%); color: var(--text-main); min-height: 100vh; padding: 24px; }
        .container { max-width: 1240px; margin: 0 auto; display: flex; flex-direction: column; gap: 24px; }
        
        /* Header */
        header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; padding-bottom: 16px; border-bottom: 1px solid var(--border-glass); }
        .logo-box { display: flex; align-items: center; gap: 14px; }
        .logo-icon { width: 44px; height: 44px; border-radius: 12px; background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue)); display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 20px rgba(0, 229, 255, 0.35); }
        h1 { font-size: 24px; font-weight: 800; letter-spacing: -0.5px; }
        .subtitle { font-size: 13px; color: var(--text-muted); display: flex; align-items: center; gap: 8px; margin-top: 2px; }
        
        .header-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .badge { display: inline-flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px; font-size: 12px; font-weight: 600; font-family: 'JetBrains Mono', monospace; background: var(--bg-card); border: 1px solid var(--border-glass); }
        .badge-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent-emerald); box-shadow: 0 0 10px var(--accent-emerald); }
        .badge-dot.pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }

        /* Main Cards Grid */
        .grid-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 20px; }
        .card { background: var(--bg-card); border: 1px solid var(--border-glass); border-radius: 22px; padding: 24px; backdrop-filter: blur(16px); transition: all 0.25s ease; box-shadow: 0 12px 36px rgba(0, 0, 0, 0.4); display: flex; flex-direction: column; justify-content: space-between; }
        .card:hover { transform: translateY(-3px); border-color: rgba(99, 140, 255, 0.25); }
        
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .card-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.8px; font-weight: 700; color: var(--text-muted); }
        .card-val { font-family: 'JetBrains Mono', monospace; font-size: 48px; font-weight: 800; letter-spacing: -1px; line-height: 1.1; margin: 10px 0 6px; }
        .card-val span { font-size: 24px; font-weight: 600; margin-left: 4px; color: var(--text-muted); }
        
        .card-status { margin-top: 14px; font-size: 12px; display: flex; justify-content: space-between; align-items: center; padding-top: 10px; border-top: 1px solid rgba(255, 255, 255, 0.06); color: var(--text-muted); }
        .status-pill { padding: 4px 10px; border-radius: 12px; font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
        .pill-ideal { background: rgba(0, 242, 169, 0.15); color: var(--accent-emerald); }
        .pill-warn { background: rgba(255, 179, 0, 0.15); color: var(--accent-amber); }
        .pill-danger { background: rgba(255, 82, 82, 0.15); color: var(--accent-rose); }

        /* 24h Daily Statistics Strip */
        .daily-stats { background: var(--bg-card); border: 1px solid var(--border-glass); border-radius: 20px; padding: 20px 26px; display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); }
        .stat-item { display: flex; flex-direction: column; gap: 4px; }
        .stat-label { font-size: 11px; text-transform: uppercase; color: var(--text-dim); letter-spacing: 0.5px; font-weight: 600; }
        .stat-value { font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 700; color: var(--text-main); }
        .stat-value small { font-size: 12px; font-weight: 500; color: var(--text-muted); }

        /* Chart Section */
        .chart-card { background: var(--bg-card); border: 1px solid var(--border-glass); border-radius: 22px; padding: 26px; position: relative; box-shadow: 0 12px 36px rgba(0,0,0,0.4); }
        .chart-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
        .chart-legend { display: flex; gap: 18px; font-size: 13px; font-weight: 600; }
        .legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
        .legend-hum { background: var(--accent-cyan); box-shadow: 0 0 10px var(--accent-cyan); }
        .legend-temp { background: var(--accent-amber); box-shadow: 0 0 10px var(--accent-amber); }
        
        .chart-wrapper { width: 100%; height: 270px; position: relative; }
        svg.chart-svg { width: 100%; height: 100%; overflow: visible; display: block; }
        .grid-line { stroke: rgba(255, 255, 255, 0.06); stroke-dasharray: 4 4; }
        .chart-time-label { fill: #94a3b8; font-size: 11px; font-weight: 600; font-family: 'JetBrains Mono', monospace; }

        /* Controls & Actions */
        .actions-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px; background: var(--bg-card); padding: 16px 24px; border-radius: 18px; border: 1px solid var(--border-glass); box-shadow: 0 10px 30px rgba(0,0,0,0.3); }
        .btn-group { display: flex; gap: 12px; flex-wrap: wrap; }
        button, select { background: rgba(255, 255, 255, 0.08); border: 1px solid var(--border-glass); color: var(--text-main); padding: 10px 18px; border-radius: 12px; font-size: 13px; font-weight: 700; cursor: pointer; transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 8px; font-family: inherit; }
        select option { background-color: #0d172e; color: #f8fafc; padding: 10px; font-size: 13px; }
        button:hover, select:hover { background: rgba(255, 255, 255, 0.14); border-color: rgba(99, 140, 255, 0.35); }
        button.btn-primary { background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue)); color: #060913; border: none; font-weight: 800; }
        button.btn-primary:hover { filter: brightness(1.1); box-shadow: 0 4px 18px rgba(0, 229, 255, 0.4); transform: translateY(-1px); }

        footer { text-align: center; font-size: 12px; color: var(--text-dim); padding: 12px 0; }
    </style>
</head>
<body>
    <div class="container">
        <!-- Top Navigation -->
        <header>
            <div class="logo-box">
                <div class="logo-icon">💧</div>
                <div>
                    <h1>ESP32 Climate Monitor</h1>
                    <div class="subtitle">
                        <span>GPIO 22 (DHT11)</span> •
                        <span id="wifiSsid">Tech Vue</span> •
                        <span id="currentTime">--:--:--</span>
                    </div>
                </div>
            </div>
            <div class="header-meta">
                <div class="badge">
                    <div class="badge-dot pulse" id="connDot"></div>
                    <span id="connStatus">Live Connected</span>
                </div>
                <div class="badge" title="Internet NTP Sync Status">
                    <span>🌐</span>
                    <span id="tzBadge">IST (UTC+5:30)</span>
                </div>
                <div class="badge" id="rssiBadge">📶 - dBm</div>
            </div>
        </header>

        <!-- Metric Cards -->
        <div class="grid-cards">
            <!-- Humidity Card -->
            <div class="card" style="border-top: 3px solid var(--accent-cyan);">
                <div class="card-header">
                    <span class="card-title">Relative Humidity</span>
                    <span class="card-icon">💧</span>
                </div>
                <div class="card-val" id="valHumidity">--<span style="color: var(--accent-cyan);">%</span></div>
                <div class="card-status">
                    <span>Comfort Level:</span>
                    <span class="status-pill pill-ideal" id="pillHumidity">Comfortable</span>
                </div>
            </div>

            <!-- Temperature Card -->
            <div class="card" style="border-top: 3px solid var(--accent-amber);">
                <div class="card-header">
                    <span class="card-title">Ambient Temperature</span>
                    <span class="card-icon">🌡️</span>
                </div>
                <div class="card-val" id="valTemp">--<span id="unitTemp" style="color: var(--accent-amber);">°C</span></div>
                <div class="card-status">
                    <span>Target Range:</span>
                    <span class="status-pill pill-ideal" id="pillTemp">Optimal</span>
                </div>
            </div>

            <!-- Heat Index Card -->
            <div class="card" style="border-top: 3px solid var(--accent-rose);">
                <div class="card-header">
                    <span class="card-title">Heat Index (Perceived)</span>
                    <span class="card-icon">🔥</span>
                </div>
                <div class="card-val" id="valHeatIndex">--<span style="color: var(--accent-rose);">°C</span></div>
                <div class="card-status">
                    <span>Health Risk:</span>
                    <span class="status-pill pill-ideal" id="pillHeatIndex">Normal</span>
                </div>
            </div>

            <!-- Device Health / Diagnostics -->
            <div class="card" style="border-top: 3px solid var(--accent-purple);">
                <div class="card-header">
                    <span class="card-title">Device Telemetry</span>
                    <span class="card-icon">⚡</span>
                </div>
                <div style="display: flex; flex-direction: column; gap: 8px; font-size: 13px; margin-top: 4px;">
                    <div style="display: flex; justify-content: space-between;"><span style="color: var(--text-muted);">IP Address:</span><strong id="valIp">192.168.1.36</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span style="color: var(--text-muted);">Uptime:</span><strong id="valUptime">--</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span style="color: var(--text-muted);">Samples:</span><strong id="valReads">--</strong></div>
                </div>
                <div class="card-status">
                    <span>NTP Time Status:</span>
                    <strong style="color: var(--accent-emerald);" id="valNtpStatus">Synced (Internet)</strong>
                </div>
            </div>
        </div>

        <!-- 24-Hour Daily Analytics Strip -->
        <div class="daily-stats">
            <div class="stat-item">
                <span class="stat-label">Min Humidity (24h)</span>
                <span class="stat-value" id="statMinHum">--%</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Max Humidity (24h)</span>
                <span class="stat-value" id="statMaxHum">--%</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Min Temperature (24h)</span>
                <span class="stat-value" id="statMinTemp">--°C</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Max Temperature (24h)</span>
                <span class="stat-value" id="statMaxTemp">--°C</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Logged Points</span>
                <span class="stat-value" id="statLogPoints">0 <small>/ 720</small></span>
            </div>
        </div>

        <!-- Chart Section -->
        <div class="chart-card">
            <div class="chart-header">
                <div>
                    <h3 style="font-size: 16px; font-weight: 700;">Recorded History & Trend Log</h3>
                    <p style="font-size: 12px; color: var(--text-muted); margin-top: 2px;" id="chartSubtitle">Timeline of logged readings</p>
                </div>
                <div class="chart-legend">
                    <div><span class="legend-dot legend-hum"></span>Humidity (%)</div>
                    <div><span class="legend-dot legend-temp"></span>Temperature (°C)</div>
                </div>
            </div>
            <div class="chart-wrapper">
                <svg class="chart-svg" id="chartSvg" viewBox="0 0 1000 240" preserveAspectRatio="none">
                    <defs>
                        <linearGradient id="humGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="#00f2fe" stop-opacity="0.3"/>
                            <stop offset="100%" stop-color="#00f2fe" stop-opacity="0.0"/>
                        </linearGradient>
                        <linearGradient id="tempGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="#f6d365" stop-opacity="0.25"/>
                            <stop offset="100%" stop-color="#f6d365" stop-opacity="0.0"/>
                        </linearGradient>
                    </defs>
                    <!-- Horizontal Grid Lines -->
                    <line x1="0" y1="20" x2="1000" y2="20" class="grid-line"></line>
                    <line x1="0" y1="75" x2="1000" y2="75" class="grid-line"></line>
                    <line x1="0" y1="130" x2="1000" y2="130" class="grid-line"></line>
                    <line x1="0" y1="185" x2="1000" y2="185" class="grid-line"></line>
                    <line x1="0" y1="215" x2="1000" y2="215" stroke="rgba(255,255,255,0.15)"></line>

                    <!-- Filled Areas under Curves -->
                    <polygon id="areaHumidity" fill="url(#humGrad)" points=""></polygon>
                    <polygon id="areaTemp" fill="url(#tempGrad)" points=""></polygon>

                    <!-- Polylines -->
                    <polyline id="polyHumidity" fill="none" stroke="#00f2fe" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points=""></polyline>
                    <polyline id="polyTemp" fill="none" stroke="#f6d365" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points=""></polyline>
                    
                    <!-- Dynamic Time Axis Markers based on available data only -->
                    <text x="15" y="235" id="timeLabelStart" class="chart-time-label">--:--</text>
                    <text x="500" y="235" id="timeLabelMid" class="chart-time-label" text-anchor="middle">--:--</text>
                    <text x="985" y="235" id="timeLabelEnd" class="chart-time-label" text-anchor="end">--:--</text>
                </svg>
            </div>
        </div>

        <!-- Controls Toolbar -->
        <div class="actions-bar">
            <div class="btn-group">
                <button id="btnToggleUnit">🌡️ Switch to °F</button>
                <button id="btnDownloadCsv" class="btn-primary">📥 Download Full Daily CSV Log</button>
                <button id="btnSyncBrowserTime" title="Sync ESP32 internal clock with your PC clock">🕒 Sync Clock with PC</button>
            </div>
            <div class="btn-group">
                <select id="tzSelect" title="Select Timezone">
                    <option value="19800:0:IST (UTC+5:30)">India (IST, UTC+5:30)</option>
                    <option value="0:0:UTC (UTC+0:00)">UTC / GMT</option>
                    <option value="14400:0:GST (UTC+4:00)">Dubai / UAE (GST, UTC+4:00)</option>
                    <option value="28800:0:SGT (UTC+8:00)">Singapore (SGT, UTC+8:00)</option>
                    <option value="-18000:3600:EST (UTC-5:00)">US Eastern (EST/EDT)</option>
                    <option value="-28800:3600:PST (UTC-8:00)">US Pacific (PST/PDT)</option>
                    <option value="3600:3600:CET (UTC+1:00)">Europe Central (CET)</option>
                </select>
                <button id="btnRefresh">🔄 Refresh Now</button>
            </div>
        </div>

        <footer>
            ESP32 + DHT11 Climate Telemetry • Connected on COM3 & Tech Vue Network
        </footer>
    </div>

    <script>
        let showFahrenheit = false;
        let isFetching = false;

        async function fetchSensorData() {
            if (isFetching) return;
            isFetching = true;

            try {
                const res = await fetch('/api/data?t=' + Date.now());
                if (!res.ok) throw new Error("HTTP " + res.status);
                const d = await res.json();

                // Live values
                document.getElementById('valHumidity').childNodes[0].textContent = d.humidity.toFixed(1);
                
                if (showFahrenheit) {
                    document.getElementById('valTemp').childNodes[0].textContent = d.temperature_f.toFixed(1);
                    document.getElementById('unitTemp').textContent = '°F';
                } else {
                    document.getElementById('valTemp').childNodes[0].textContent = d.temperature.toFixed(1);
                    document.getElementById('unitTemp').textContent = '°C';
                }

                document.getElementById('valHeatIndex').childNodes[0].textContent = d.heat_index.toFixed(1);
                document.getElementById('valUptime').textContent = d.uptime_str;
                document.getElementById('valIp').textContent = d.ip;
                document.getElementById('valReads').textContent = `${d.reads} (err: ${d.errors})`;
                document.getElementById('rssiBadge').textContent = `📶 ${d.rssi} dBm`;

                if (d.time_str) {
                    document.getElementById('currentTime').textContent = d.time_str;
                }
                if (d.timezone) {
                    document.getElementById('tzBadge').textContent = d.timezone;
                }
                document.getElementById('valNtpStatus').textContent = d.ntp_synced ? 'Synced (Internet NTP)' : 'Awaiting NTP / Manual Sync';
                document.getElementById('valNtpStatus').style.color = d.ntp_synced ? 'var(--accent-emerald)' : 'var(--accent-amber)';

                // Comfort Level Badges
                const h = d.humidity;
                const pillH = document.getElementById('pillHumidity');
                if (h < 30) { pillH.className = 'status-pill pill-warn'; pillH.textContent = 'Too Dry'; }
                else if (h <= 60) { pillH.className = 'status-pill pill-ideal'; pillH.textContent = 'Ideal Comfort'; }
                else if (h <= 70) { pillH.className = 'status-pill pill-warn'; pillH.textContent = 'High Humidity'; }
                else { pillH.className = 'status-pill pill-danger'; pillH.textContent = 'Excessive Humidity'; }

                // Temperature Badges
                const t = d.temperature;
                const pillT = document.getElementById('pillTemp');
                if (t < 18) { pillT.className = 'status-pill pill-warn'; pillT.textContent = 'Cold'; }
                else if (t <= 28) { pillT.className = 'status-pill pill-ideal'; pillT.textContent = 'Ideal Resin/Print'; }
                else { pillT.className = 'status-pill pill-warn'; pillT.textContent = 'Warm'; }

                // Online indicator
                document.getElementById('connDot').className = 'badge-dot pulse';
                document.getElementById('connStatus').textContent = 'Live Connected';
            } catch (err) {
                document.getElementById('connDot').className = 'badge-dot';
                document.getElementById('connDot').style.background = 'var(--accent-rose)';
                document.getElementById('connDot').style.boxShadow = '0 0 10px var(--accent-rose)';
                document.getElementById('connStatus').textContent = 'Connection Reconnecting...';
            } finally {
                isFetching = false;
            }
        }

        async function fetchHistoryData() {
            try {
                const res = await fetch('/api/history?t=' + Date.now());
                if (!res.ok) return;
                const d = await res.json();

                if (d.stats) {
                    document.getElementById('statMinHum').textContent = d.stats.min_hum > 100 ? '--%' : d.stats.min_hum.toFixed(1) + '%';
                    document.getElementById('statMaxHum').textContent = d.stats.max_hum < 0 ? '--%' : d.stats.max_hum.toFixed(1) + '%';
                    document.getElementById('statMinTemp').textContent = d.stats.min_temp > 100 ? '--°C' : d.stats.min_temp.toFixed(1) + '°C';
                    document.getElementById('statMaxTemp').textContent = d.stats.max_temp < -100 ? '--°C' : d.stats.max_temp.toFixed(1) + '°C';
                    document.getElementById('statLogPoints').innerHTML = `${d.count} <small>/ 720</small>`;
                }

                if (d.logs && d.logs.length > 1) {
                    renderSvgChart(d.logs);
                }
            } catch (e) {}
        }

        function formatEpochToTime(epochSec) {
            if (!epochSec || epochSec < 100000) return '--:--';
            const d = new Date(epochSec * 1000);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }

        function renderSvgChart(logs) {
            // Filter to actual recorded data points only
            const chartData = logs.filter(item => item && item.temp !== undefined && item.hum !== undefined);
            if (chartData.length === 0) return;

            const width = 1000;
            const height = 240;
            const padTop = 20;
            const padBottom = 35; // Space for dynamic time labels
            const plotH = height - padTop - padBottom;
            const baselineY = height - padBottom;

            // Compute dynamic Min and Max strictly from available recorded data with comfort padding
            const hums = chartData.map(d => d.hum);
            const temps = chartData.map(d => d.temp);
            const rawMinH = Math.min(...hums);
            const rawMaxH = Math.max(...hums);
            const rawMinT = Math.min(...temps);
            const rawMaxT = Math.max(...temps);

            // Give reasonable ranges around available data
            const minH = Math.max(0, Math.floor(rawMinH - 2));
            const maxH = Math.min(100, Math.ceil(rawMaxH + 2));
            const rangeH = (maxH - minH) > 0 ? (maxH - minH) : 10;

            const minT = Math.floor(rawMinT - 1);
            const maxT = Math.ceil(rawMaxT + 1);
            const rangeT = (maxT - minT) > 0 ? (maxT - minT) : 5;

            let ptsHum = [];
            let ptsTemp = [];

            if (chartData.length === 1) {
                // If only 1 reading exists, draw single horizontal level across
                const yH = baselineY - ((chartData[0].hum - minH) / rangeH) * plotH;
                const yT = baselineY - ((chartData[0].temp - minT) / rangeT) * plotH;
                ptsHum = [`0,${yH.toFixed(1)}`, `${width},${yH.toFixed(1)}`];
                ptsTemp = [`0,${yT.toFixed(1)}`, `${width},${yT.toFixed(1)}`];
            } else {
                ptsHum = chartData.map((item, idx) => {
                    const x = (idx / (chartData.length - 1)) * width;
                    const y = baselineY - ((item.hum - minH) / rangeH) * plotH;
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                });

                ptsTemp = chartData.map((item, idx) => {
                    const x = (idx / (chartData.length - 1)) * width;
                    const y = baselineY - ((item.temp - minT) / rangeT) * plotH;
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                });
            }

            const strHum = ptsHum.join(' ');
            const strTemp = ptsTemp.join(' ');

            const polyHum = document.getElementById('polyHumidity');
            const polyT = document.getElementById('polyTemp');
            if (polyHum) polyHum.setAttribute('points', strHum);
            if (polyT) polyT.setAttribute('points', strTemp);

            // Shaded area
            const areaHum = document.getElementById('areaHumidity');
            const areaT = document.getElementById('areaTemp');
            if (areaHum) areaHum.setAttribute('points', `0,${baselineY} ${strHum} ${width},${baselineY}`);
            if (areaT) areaT.setAttribute('points', `0,${baselineY} ${strTemp} ${width},${baselineY}`);

            // Dynamic Time Axis from AVAILABLE DATA ONLY
            const firstEntry = chartData[0];
            const lastEntry = chartData[chartData.length - 1];
            const midIndex = Math.floor(chartData.length / 2);
            const midEntry = chartData[midIndex];

            const lblStart = document.getElementById('timeLabelStart');
            const lblMid = document.getElementById('timeLabelMid');
            const lblEnd = document.getElementById('timeLabelEnd');

            if (lblStart) lblStart.textContent = formatEpochToTime(firstEntry.t);
            if (lblEnd) lblEnd.textContent = formatEpochToTime(lastEntry.t);
            if (lblMid) {
                lblMid.textContent = (chartData.length > 2) ? formatEpochToTime(midEntry.t) : '';
            }

            const sub = document.getElementById('chartSubtitle');
            if (sub) {
                sub.textContent = `${chartData.length} recorded log entries (${formatEpochToTime(firstEntry.t)} → ${formatEpochToTime(lastEntry.t)})`;
            }
        }

        // Timezone selection
        document.getElementById('tzSelect').addEventListener('change', async (e) => {
            const [gmtOffset, daylight, name] = e.target.value.split(':');
            try {
                await fetch(`/api/set_time?gmt=${gmtOffset}&daylight=${daylight}&tz_name=${encodeURIComponent(name)}`);
                fetchSensorData();
            } catch (err) {}
        });

        // Sync Browser PC Clock
        document.getElementById('btnSyncBrowserTime').addEventListener('click', async () => {
            const epoch = Math.floor(Date.now() / 1000);
            const tzOffsetMin = -new Date().getTimezoneOffset(); // in minutes
            const gmtSec = tzOffsetMin * 60;
            try {
                await fetch(`/api/set_time?epoch=${epoch}&gmt=${gmtSec}&tz_name=Browser Local`);
                alert('Time synchronized with your computer clock!');
                fetchSensorData();
            } catch (err) {
                alert('Failed to sync time: ' + err.message);
            }
        });

        // Download CSV
        document.getElementById('btnDownloadCsv').addEventListener('click', () => {
            window.location.href = '/api/history.csv';
        });

        // Toggle Unit
        document.getElementById('btnToggleUnit').addEventListener('click', () => {
            showFahrenheit = !showFahrenheit;
            document.getElementById('btnToggleUnit').textContent = showFahrenheit ? '🌡️ Switch to °C' : '🌡️ Switch to °F';
            fetchSensorData();
        });

        document.getElementById('btnRefresh').addEventListener('click', () => {
            fetchSensorData();
            fetchHistoryData();
        });

        // Poll intervals
        setInterval(fetchSensorData, 2000);
        setInterval(fetchHistoryData, 10000);

        fetchSensorData();
        fetchHistoryData();
    </script>
</body>
</html>
)rawliteral";

// -----------------------------------------------------------------------------
// DHT11 Sensor Reading Routine
// -----------------------------------------------------------------------------
void readDhtSensor() {
  unsigned long now = millis();
  if (now - lastSensorReadTime < SENSOR_READ_INTERVAL_MS && lastSensorReadTime != 0) {
    return;
  }
  lastSensorReadTime = now;

  float h = dht.readHumidity();
  float t = dht.readTemperature();

  if (isnan(h) || isnan(t)) {
    sensorReadOk = false;
    sensorErrorCount++;
  } else {
    currentHumidity  = h;
    currentTempC     = t;
    currentTempF     = (t * 1.8) + 32.0;
    currentHeatIndex = dht.computeHeatIndex(t, h, false);
    sensorReadOk     = true;
    totalReadCount++;

    // Track min / max for today
    if (t < minTempToday) minTempToday = t;
    if (t > maxTempToday) maxTempToday = t;
    if (h < minHumToday)  minHumToday  = h;
    if (h > maxHumToday)  maxHumToday  = h;

    // Check if it's time to store a daily log snapshot
    if (now - lastLogTime >= LOG_INTERVAL_MS || lastLogTime == 0) {
      lastLogTime = now;
      time_t currentEpoch;
      time(&currentEpoch);

      dailyLogs[logHead] = { (uint32_t)currentEpoch, t, h };
      logHead = (logHead + 1) % MAX_LOG_ENTRIES;
      if (logCount < MAX_LOG_ENTRIES) logCount++;
    }

    // Stream live reading over Serial / COM port as clean JSON for companion apps
    time_t nowTime;
    time(&nowTime);
    Serial.printf("{\"temp\":%.1f,\"hum\":%.1f,\"heat_index\":%.1f,\"rssi\":%d,\"epoch\":%lu,\"reads\":%lu,\"tz\":\"%s\"}\n",
      currentTempC, currentHumidity, currentHeatIndex, WiFi.RSSI(), (unsigned long)nowTime, totalReadCount, timezoneName);
  }
}

// -----------------------------------------------------------------------------
// NTP Internet Time Synchronization
// -----------------------------------------------------------------------------
void syncNtpTime() {
  if (WiFi.status() == WL_CONNECTED) {
    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer1, ntpServer2, ntpServer3);
    struct tm timeinfo;
    if (getLocalTime(&timeinfo, 2000)) {
      ntpSynced = true;
      Serial.printf("[NTP] Internet Time Synced: %04d-%02d-%02d %02d:%02d:%02d (%s)\n",
                    timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
                    timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec, timezoneName);
    }
  }
}

// -----------------------------------------------------------------------------
// Time & String Formatters
// -----------------------------------------------------------------------------
String getFormattedTime(time_t raw) {
  struct tm* t = localtime(&raw);
  if (!t) return String("--:--:--");
  char buf[32];
  snprintf(buf, sizeof(buf), "%02d:%02d:%02d", t->tm_hour, t->tm_min, t->tm_sec);
  return String(buf);
}

String getFormattedDate(time_t raw) {
  struct tm* t = localtime(&raw);
  if (!t) return String("----/--/--");
  char buf[32];
  snprintf(buf, sizeof(buf), "%04d-%02d-%02d", t->tm_year + 1900, t->tm_mon + 1, t->tm_mday);
  return String(buf);
}

String getUptimeFormatted() {
  unsigned long totalSeconds = (millis() - bootTimeMs) / 1000;
  unsigned long hours = totalSeconds / 3600;
  unsigned long minutes = (totalSeconds % 3600) / 60;
  unsigned long seconds = totalSeconds % 60;

  char buf[32];
  snprintf(buf, sizeof(buf), "%luh %lum %lus", hours, minutes, seconds);
  return String(buf);
}

// -----------------------------------------------------------------------------
// Serial COM Port Commands Listener
// Allows companion websites or tools on COM port to sync time, query or configure
// Command Format:
//   SYNC_TIME:<epoch>:<timezone_name>
//   GET_DATA
// -----------------------------------------------------------------------------
void checkSerialCommands() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd.startsWith("SYNC_TIME:")) {
      // Format: SYNC_TIME:1731478200:IST (UTC+5:30)
      int firstColon = cmd.indexOf(':');
      int secondColon = cmd.indexOf(':', firstColon + 1);
      if (firstColon > 0) {
        String epochStr = (secondColon > 0) ? cmd.substring(firstColon + 1, secondColon) : cmd.substring(firstColon + 1);
        unsigned long epoch = strtoul(epochStr.c_str(), NULL, 10);
        if (epoch > 100000) {
          struct timeval tv = { (time_t)epoch, 0 };
          settimeofday(&tv, NULL);
          ntpSynced = true;
          if (secondColon > 0) {
            String tz = cmd.substring(secondColon + 1);
            strncpy(timezoneName, tz.c_str(), sizeof(timezoneName) - 1);
          }
          Serial.printf("[COM SYNC] Clock set to epoch %lu (%s)\n", epoch, timezoneName);
        }
      }
    } else if (cmd.startsWith("SET_CONFIG:")) {
      // Format: SET_CONFIG:<ssid>:<password>:<dht_pin>:<led_pin>
      // Example: SET_CONFIG:Tech Vue:maxmus8085:22:2
      int p1 = cmd.indexOf(':');
      int p2 = cmd.indexOf(':', p1 + 1);
      int p3 = cmd.indexOf(':', p2 + 1);
      int p4 = cmd.indexOf(':', p3 + 1);
      if (p1 > 0 && p2 > 0 && p3 > 0) {
        String newSsid = cmd.substring(p1 + 1, p2);
        String newPass = cmd.substring(p2 + 1, p3);
        int newDhtPin = (p4 > 0) ? cmd.substring(p3 + 1, p4).toInt() : cmd.substring(p3 + 1).toInt();
        int newLedPin = (p4 > 0) ? cmd.substring(p4 + 1).toInt() : statusLedPin;

        prefs.begin("wifi_cfg", false);
        if (newSsid.length() > 0) prefs.putString("ssid", newSsid);
        prefs.putString("pass", newPass);
        if (newDhtPin > 0) prefs.putInt("dht_pin", newDhtPin);
        if (newLedPin > 0) prefs.putInt("led_pin", newLedPin);
        prefs.end();

        if (newSsid.length() > 0) wifi_ssid = newSsid;
        wifi_password = newPass;
        if (newDhtPin > 0) dhtPin = newDhtPin;
        if (newLedPin > 0) statusLedPin = newLedPin;

        Serial.printf("[CONFIG UPDATE] Wi-Fi: %s, DHT Pin: GPIO %d, LED: GPIO %d. Reconnecting...\n",
                      wifi_ssid.c_str(), dhtPin, statusLedPin);

        dht = DHT(dhtPin, DHTTYPE);
        dht.begin();
        pinMode(statusLedPin, OUTPUT);

        WiFi.disconnect();
        WiFi.begin(wifi_ssid.c_str(), wifi_password.c_str());
      }
    } else if (cmd.startsWith("SET_WIFI:")) {
      // Format: SET_WIFI:<ssid>:<password>
      int c1 = cmd.indexOf(':');
      int c2 = cmd.indexOf(':', c1 + 1);
      if (c1 > 0 && c2 > 0) {
        String newSsid = cmd.substring(c1 + 1, c2);
        String newPass = cmd.substring(c2 + 1);
        if (newSsid.length() > 0) {
          prefs.begin("wifi_cfg", false);
          prefs.putString("ssid", newSsid);
          prefs.putString("pass", newPass);
          prefs.end();
          wifi_ssid = newSsid;
          wifi_password = newPass;
          Serial.printf("[WIFI CONFIG] New Wi-Fi saved: %s. Connecting...\n", newSsid.c_str());
          WiFi.disconnect();
          WiFi.begin(wifi_ssid.c_str(), wifi_password.c_str());
        }
      }
    } else if (cmd == "GET_DATA") {
      time_t nowTime;
      time(&nowTime);
      Serial.printf("{\"status\":\"%s\",\"temp\":%.1f,\"hum\":%.1f,\"heat_index\":%.1f,\"epoch\":%lu,\"tz\":\"%s\"}\n",
        sensorReadOk ? "OK" : "ERROR", currentTempC, currentHumidity, currentHeatIndex, (unsigned long)nowTime, timezoneName);
    }
  }
}

// -----------------------------------------------------------------------------
// Web Server Route Handlers
// -----------------------------------------------------------------------------

void handleRoot() {
  readDhtSensor();
  server.sendHeader("Connection", "close");
  server.send(200, "text/html", INDEX_HTML);
}

void handleApiData() {
  readDhtSensor();

  time_t nowTime;
  time(&nowTime);
  String timeStr = getFormattedTime(nowTime);

  char jsonBuffer[512];
  unsigned long uptimeSec = (millis() - bootTimeMs) / 1000;
  int rssiVal = WiFi.RSSI();
  String ipStr = WiFi.localIP().toString();
  String uptimeStr = getUptimeFormatted();

  snprintf(jsonBuffer, sizeof(jsonBuffer),
    "{"
      "\"status\":\"%s\","
      "\"humidity\":%.1f,"
      "\"temperature\":%.1f,"
      "\"temperature_f\":%.1f,"
      "\"heat_index\":%.1f,"
      "\"rssi\":%d,"
      "\"uptime\":%lu,"
      "\"uptime_str\":\"%s\","
      "\"ip\":\"%s\","
      "\"time_str\":\"%s\","
      "\"timezone\":\"%s\","
      "\"ntp_synced\":%s,"
      "\"reads\":%lu,"
      "\"errors\":%lu"
    "}",
    sensorReadOk ? "OK" : "SENSOR_ERROR",
    currentHumidity,
    currentTempC,
    currentTempF,
    currentHeatIndex,
    rssiVal,
    uptimeSec,
    uptimeStr.c_str(),
    ipStr.c_str(),
    timeStr.c_str(),
    timezoneName,
    ntpSynced ? "true" : "false",
    totalReadCount,
    sensorErrorCount
  );

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Cache-Control", "no-cache, no-store, must-revalidate");
  server.send(200, "application/json", jsonBuffer);
}

void handleApiHistory() {
  String json = "{\"count\":" + String(logCount) + ",\"stats\":{";
  json += "\"min_temp\":" + String(minTempToday) + ",";
  json += "\"max_temp\":" + String(maxTempToday) + ",";
  json += "\"min_hum\":" + String(minHumToday) + ",";
  json += "\"max_hum\":" + String(maxHumToday) + "},\"logs\":[";

  int startIndex = (logCount == MAX_LOG_ENTRIES) ? logHead : 0;
  for (int i = 0; i < logCount; i++) {
    int idx = (startIndex + i) % MAX_LOG_ENTRIES;
    if (i > 0) json += ",";
    json += "{\"t\":" + String(dailyLogs[idx].timestamp) + ",";
    json += "\"temp\":" + String(dailyLogs[idx].temp, 1) + ",";
    json += "\"hum\":" + String(dailyLogs[idx].hum, 1) + "}";
  }
  json += "]}";

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Cache-Control", "no-cache");
  server.send(200, "application/json", json);
}

void handleApiHistoryCsv() {
  String csv = "Timestamp,Date,Time,Temperature_C,Humidity_Pct\n";
  int startIndex = (logCount == MAX_LOG_ENTRIES) ? logHead : 0;
  for (int i = 0; i < logCount; i++) {
    int idx = (startIndex + i) % MAX_LOG_ENTRIES;
    time_t raw = dailyLogs[idx].timestamp;
    csv += String(raw) + ",";
    csv += getFormattedDate(raw) + ",";
    csv += getFormattedTime(raw) + ",";
    csv += String(dailyLogs[idx].temp, 1) + ",";
    csv += String(dailyLogs[idx].hum, 1) + "\n";
  }

  server.sendHeader("Content-Disposition", "attachment; filename=esp32_humidity_daily_log.csv");
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/csv", csv);
}

void handleSetTime() {
  if (server.hasArg("gmt")) {
    gmtOffset_sec = server.arg("gmt").toInt();
  }
  if (server.hasArg("daylight")) {
    daylightOffset_sec = server.arg("daylight").toInt();
  }
  if (server.hasArg("tz_name")) {
    String tz = server.arg("tz_name");
    strncpy(timezoneName, tz.c_str(), sizeof(timezoneName) - 1);
  }
  if (server.hasArg("epoch")) {
    unsigned long epoch = strtoul(server.arg("epoch").c_str(), NULL, 10);
    struct timeval tv = { (time_t)epoch, 0 };
    settimeofday(&tv, NULL);
    ntpSynced = true;
  } else {
    syncNtpTime();
  }

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", "{\"status\":\"OK\",\"message\":\"Timezone/Clock updated\"}");
}

void handleNotFound() {
  server.send(404, "text/plain", "404: Not Found on ESP32");
}

// -----------------------------------------------------------------------------
// Arduino Setup
// -----------------------------------------------------------------------------
void setup() {
  bootTimeMs = millis();
  Serial.begin(115200);
  delay(400);

  // Load stored preferences (Wi-Fi, sensor pin, LED pin)
  prefs.begin("wifi_cfg", true);
  String savedSsid = prefs.getString("ssid", "");
  String savedPass = prefs.getString("pass", "");
  int savedDht = prefs.getInt("dht_pin", 0);
  int savedLed = prefs.getInt("led_pin", 0);
  prefs.end();

  if (savedSsid.length() > 0) {
    wifi_ssid = savedSsid;
    wifi_password = savedPass;
  }
  if (savedDht > 0) dhtPin = savedDht;
  if (savedLed > 0) statusLedPin = savedLed;

  // Initialize Status LED
  pinMode(statusLedPin, OUTPUT);
  // Startup indicator pulse
  for (int i = 0; i < 4; i++) {
    digitalWrite(statusLedPin, HIGH);
    delay(70);
    digitalWrite(statusLedPin, LOW);
    delay(70);
  }

  Serial.println(F("\n======================================================="));
  Serial.println(F(" ESP32 + DHT11 Humidity & Daily Logger Web Server    "));
  Serial.println(F("======================================================="));
  Serial.printf("Sensor Pin     : GPIO %d (DHT11)\n", dhtPin);
  Serial.printf("Status LED Pin : GPIO %d\n", statusLedPin);
  Serial.printf("Default TZ     : %s (UTC %+ldh %+ldm)\n", timezoneName, gmtOffset_sec / 3600, (gmtOffset_sec % 3600) / 60);

  dht = DHT(dhtPin, DHTTYPE);
  dht.begin();
  Serial.println(F("[INIT] DHT11 sensor initialized."));

  // Connect Wi-Fi with flashing LED indicator
  Serial.printf("[WIFI] Connecting to \"%s\" ", wifi_ssid.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(wifi_ssid.c_str(), wifi_password.c_str());

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    digitalWrite(statusLedPin, HIGH);
    delay(200);
    digitalWrite(statusLedPin, LOW);
    delay(200);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    digitalWrite(statusLedPin, HIGH); // Solid ON when connected
    Serial.println(F("\n[WIFI] Connected successfully!"));
    Serial.printf("[WIFI] IP Address : http://%s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[WIFI] Signal     : %d dBm\n", WiFi.RSSI());
    
    // Sync Internet NTP Time
    syncNtpTime();
  } else {
    digitalWrite(statusLedPin, LOW);
    Serial.println(F("\n[WIFI WARNING] Wi-Fi connection taking longer. Operating in background..."));
  }

  // Register Web Server Endpoints
  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/data", HTTP_GET, handleApiData);
  server.on("/api/history", HTTP_GET, handleApiHistory);
  server.on("/api/history.csv", HTTP_GET, handleApiHistoryCsv);
  server.on("/api/set_time", HTTP_GET, handleSetTime);
  server.on("/api/wifi", HTTP_POST, []() {
    if (server.hasArg("ssid")) {
      String s = server.arg("ssid");
      String p = server.hasArg("password") ? server.arg("password") : "";
      prefs.begin("wifi_cfg", false);
      prefs.putString("ssid", s);
      prefs.putString("pass", p);
      prefs.end();
      server.send(200, "application/json", "{\"status\":\"OK\",\"message\":\"Wi-Fi credentials saved. Reconnecting...\"}");
      delay(500);
      WiFi.disconnect();
      WiFi.begin(s.c_str(), p.c_str());
      return;
    }
    server.send(400, "application/json", "{\"status\":\"ERROR\",\"message\":\"Missing ssid\"}");
  });
  server.onNotFound(handleNotFound);

  server.begin();
  Serial.println(F("[SERVER] HTTP Server started on port 80."));
  Serial.println(F("[SERVER] Endpoints ready:"));
  Serial.println(F("         - Dashboard   : /"));
  Serial.println(F("         - Live API    : /api/data"));
  Serial.println(F("         - History API : /api/history"));
  Serial.println(F("         - CSV Export  : /api/history.csv"));
  Serial.println(F("         - Set Clock   : /api/set_time?epoch=...&gmt=..."));
  Serial.println(F("=======================================================\n"));

  readDhtSensor();
}

// -----------------------------------------------------------------------------
// Arduino Loop
// -----------------------------------------------------------------------------
void loop() {
  server.handleClient();
  readDhtSensor();
  checkSerialCommands();

  // Periodic NTP resync every 1 hour
  if (millis() - lastNtpAttempt > 3600000 && WiFi.status() == WL_CONNECTED) {
    lastNtpAttempt = millis();
    syncNtpTime();
  }

// Wi-Fi watchdog with status LED heartbeat
  if (WiFi.status() != WL_CONNECTED) {
    digitalWrite(statusLedPin, (millis() / 500) % 2); // Slow blink when disconnected
    static unsigned long lastReconnect = 0;
    if (millis() - lastReconnect > 10000) {
      lastReconnect = millis();
      WiFi.reconnect();
    }
  } else {
    // Brief heartbeat pulse every 3 seconds to indicate normal operation
    unsigned long mod = millis() % 3000;
    if (mod < 80) {
      digitalWrite(statusLedPin, LOW); // brief blink off
    } else {
      digitalWrite(statusLedPin, HIGH);
    }
  }

  delay(2);
}
