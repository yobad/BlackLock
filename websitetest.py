

"""
Description: This is a test file created to test the website system of the BlackLock.
Objectives defined below.
Date: Apr 23, 2026
Author: The BlackLock Team 
This file was generated using Claude.ai, we put our main code file (main.py) and the word document that we 
created to design the tests as reference so that the AI could put them all together
Here's a link to the discussion: https://claude.ai/share/153a9703-121d-4fb9-8fca-2f2ae00f5fe7

========================================================
Author: BlackLock Team
 
Objective:
  - Verify start time and session length submitted on the website
    are correctly received and stored by the Pico
  - Verify the received values appear on the LCD screen
 
No motors, no load cell, no SD card needed.
 
Test case:
  Case 1 — Data submitted on website → Pico prints exact values received
                                      AND LCD displays start time + duration
 
How to run:
   1. Press on the run button at the bottom of VS Code
  4. Connect a phone/laptop to Wi-Fi "BlackLock" (pw: BlackLock2)
  5. Open browser at the IP printed in the console and showed on the LCD
  6. Enter a start time and duration, click Save Schedule
  7. Check BOTH the VS Code console AND the LCD screen
 
Expected console output after clicking Save Schedule:
  ✅ DATA RECEIVED
     start_time : HH:MM     ← must match what you typed
     duration   : X min     ← must match what you typed
 
Expected LCD after clicking Save Schedule:
  Line 1: "Start: HH:MM"
  Line 2: "Dur: X min"
"""
 
import asyncio
import network
import time
from machine import I2C, Pin
from pico_i2c_lcd import I2cLcd
 
# ── LCD config — update to match your wiring ──────────────────────────────────
I2C_ADDR = 0x27   # Change to 0x3F if 0x27 not found by the scanner
I2C_BUS  = 0
SDA_PIN  = 0
SCL_PIN  = 1
NUM_ROWS = 2
NUM_COLS = 16
 
SSID     = "BlackLock"
PASSWORD = "BlackLock2"
 
# ── State ─────────────────────────────────────────────────────────────────────
state = {
    "start_time":    None,
    "duration":      None,
    "message":       "",
    "schedule_info": "",
    "ip":            None,
}
 
# ── LCD init ──────────────────────────────────────────────────────────────────
print("Scanning I2C bus...")
i2c   = I2C(I2C_BUS, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=10000)
found = i2c.scan()
print("I2C devices found:", [hex(d) for d in found])
if I2C_ADDR not in found:
    print(f"WARNING: 0x{I2C_ADDR:02X} not found — update I2C_ADDR and re-run")
 
lcd = I2cLcd(i2c, I2C_ADDR, NUM_ROWS, NUM_COLS)
print("LCD ready.\n")
 
def lcd_show(line1="", line2=""):
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr(line1[:NUM_COLS])
    lcd.move_to(0, 1)
    lcd.putstr(line2[:NUM_COLS])
 
# ── Access point ──────────────────────────────────────────────────────────────
def create_ap(ssid, password):
    ap = network.WLAN(network.AP_IF)
    ap.config(ssid=ssid, password=password)
    ap.active(True)
    timeout = 10
    while not ap.active() and timeout > 0:
        time.sleep(0.5)
        timeout -= 1
    if ap.active():
        ip, _ = ap.ipconfig('addr4')
        print(f"AP ready  →  SSID: {ssid}  PW: {password}  IP: {ip}")
        return ip
    print("AP failed to start")
    return None
 
# ── URL helpers ───────────────────────────────────────────────────────────────
def parse_query(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params
 
def url_decode(s):
    s = s.replace("+", " ")
    out, i = [], 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            out.append(chr(int(s[i+1:i+3], 16)))
            i += 3
        else:
            out.append(s[i])
            i += 1
    return "".join(out)
 
# ── HTML ──────────────────────────────────────────────────────────────────────
def render_page(message="", schedule_info=""):
    start = state["start_time"] or ""
    dur   = str(state["duration"]) if state["duration"] else ""
    try:
        with open("index.html", "r") as f:
            html = f.read()
    except OSError:
        return "<h1>Error: index.html not found on Pico</h1>"
    html = html.replace("{{START_TIME}}",    start)
    html = html.replace("{{DURATION}}",      dur)
    html = html.replace("{{MESSAGE}}",       message)
    html = html.replace("{{SCHEDULE_INFO}}", schedule_info)
    return html
 
# ── Web server ────────────────────────────────────────────────────────────────
async def process_requests(reader, writer):
    try:
        request_line = await reader.readline()
        request_line = str(request_line, "utf-8").strip()
 
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", b"\n"):
                break
 
        if not request_line or "favicon" in request_line:
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return
 
        parts = request_line.split()
        if len(parts) < 2:
            await writer.wait_closed()
            return
 
        path  = parts[1]
        route, qs = (path.split("?", 1) if "?" in path else (path, ""))
        params  = parse_query(qs)
        message = ""
 
        if route == "/set":
            st  = params.get("start_time", "").strip()
            dur = params.get("duration",   "").strip()
            if st and dur and dur.isdigit() and int(dur) > 0:
                state["start_time"] = st
                state["duration"]   = int(dur)
                message = f"Schedule saved: {st} for {dur}min"
                state["schedule_info"] = message
                state["message"]       = message
 
                # ── Console verification ──────────────────────────────────
                print()
                print("✅ DATA RECEIVED")
                print(f"   start_time : {st}")
                print(f"   duration   : {dur} min")
                print()
 
                # ── LCD verification ──────────────────────────────────────
                # Display the received values on the LCD so the tester can
                # confirm the Pico stored what the website sent.
                # Line 1 shows the start time, line 2 shows the duration.
                lcd_show(f"Start: {st}", f"Dur: {dur} min")
 
            else:
                message = "❌ Invalid input — check time and duration fields."
                print(message)
                # Show the error on the LCD too so it's visible without a laptop
                lcd_show("Invalid input!", "Check website")
 
        elif route == "/command":
            cmd = params.get("cmd", "")
            if cmd == "EMERGENCY_UNLOCK":
                message = "Emergency unlock received."
                print("[CMD] Emergency unlock received")
                lcd_show("EMERGENCY", "Unlock received")
            elif cmd == "LOCK":
                message = "Lock command received."
                print("[CMD] Lock command received")
                lcd_show("Lock cmd", "received")
        else:
            message = state["message"]
 
        html = render_page(message, state["schedule_info"])
        response = (
            "HTTP/1.0 200 OK\r\n"
            "Content-Type: text/html\r\n"
            f"Content-Length: {len(html)}\r\n"
            "\r\n" + html
        )
        writer.write(response.encode())
        await writer.drain()
 
    except Exception as e:
        print("Request error:", e)
    finally:
        await writer.wait_closed()
 
# ── LCD clock task — cycles through connection info ──────────────────────────────────
async def idle_display_task():
    """Shows connection info on the LCD while waiting for a submission."""
    counter = 0
    while True:
        # Only update the idle display if no schedule has been received yet.
        # Once data arrives, lcd_show() in process_requests takes over.
        if state["start_time"] is None:
            # Cycle between two displays: SSID/PW and IP
            if counter % 2 == 0:
                lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")
            else:
                ip_display = state["ip"] if state["ip"] else "Loading..."
                lcd_show(f"IP: {ip_display}", "")
            counter += 1
        await asyncio.sleep(5)
 
# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    ip = create_ap(SSID, PASSWORD)
    if not ip:
        print("AP failed. Halting.")
        return
 
    # Store IP in state and show it on LCD immediately
    state["ip"] = ip
    lcd_show(f"W:{SSID}", f"P:{PASSWORD}")
    print()
 
    await asyncio.start_server(process_requests, "0.0.0.0", 80)
    await asyncio.gather(idle_display_task())
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped by user")
    except Exception as e:
        print("Fatal error:", e)