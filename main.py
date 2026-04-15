"""
Description: Main project file of BlackLock project.
Date: Apr 14, 2026
Author: Youmna Badawy
 
Architecture:
  - Pico W creates a Wi-Fi hotspot (AP mode)
  - Async web server handles /set and /command routes
  - Async weight-monitor task runs concurrently
  - Shared `state` dict is the single source of truth for all tasks
"""
 
import asyncio
import network
import time
from machine import Pin, PWM, I2C
from pico_i2c_lcd import I2cLcd
 
# ── External module imports (your existing files) ────────────────────────────
from hx711_gpio import HX711
import motor
from dcmotor import DCMotor
import machine
import utime
import os, sdcard, vfs
 
# ── Configuration ────────────────────────────────────────────────────────────
SSID      = "BlackLock"
PASSWORD  = "BlackLock2"
 
# Load cell
DATA_PIN  = Pin(5, Pin.IN, pull=Pin.PULL_DOWN)
CLOCK_PIN = Pin(6, Pin.OUT)
SCALE_F   = 1138.6597
THRESHOLD = 170          # grams — tweak after calibration
 
# Stepper motor
STEP_IN1, STEP_IN2, STEP_IN3, STEP_IN4 = 14, 12, 13, 15
STEPS_TO_CLOSE = 1028
 
# Linear actuator
LIN_IN1 = Pin(21, Pin.OUT)
LIN_IN2 = Pin(10, Pin.OUT)
LIN_ENA = PWM(Pin(11))
 
# LCD  (adjust I2C address / pins to match your wiring)
I2C_ADDR   = 0x27
I2C_NUM_ROWS, I2C_NUM_COLS = 2, 16
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400_000)
lcd = I2cLcd(i2c, I2C_ADDR, I2C_NUM_ROWS, I2C_NUM_COLS)
 
# ── Shared state (acts as the "database" between all async tasks) ─────────────
state = {
    "start_time": None,       # "HH:MM"  string from the web form
    "duration":   None,       # int, minutes
    "session_active": False,  # True while a study session is running
    "phone_in_box":   False,  # True once weight sensor detects phone
    "emergency_unlock": False,# Set to True by the web emergency button
    "message":   "",          # Status message shown on the web page
    "schedule_info": "",      # Friendly schedule string shown on the web page
}
 
# ── Hardware setup ───────────────────────────────────────────────────────────
hx711 = HX711(CLOCK_PIN, DATA_PIN)
hx711.tare()
hx711.set_scale(SCALE_F)
 
stepper = motor.FullStepMotor.frompins(STEP_IN1, STEP_IN2, STEP_IN3, STEP_IN4)
lin_act = DCMotor(LIN_IN1, LIN_IN2, LIN_ENA)
 
# ── Access point ─────────────────────────────────────────────────────────────
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
 
# ── HTML helpers ─────────────────────────────────────────────────────────────
def render_page(message="", schedule_info=""):
    """Read index.html and fill in the {{PLACEHOLDERS}}."""
    start  = state["start_time"] or ""
    dur    = str(state["duration"]) if state["duration"] else ""
    try:
        with open("index.html", "r") as f:
            html = f.read()
    except OSError:
        html = "<h1>Error: index.html not found</h1>"
        return html
 
    html = html.replace("{{START_TIME}}",    start)
    html = html.replace("{{DURATION}}",      dur)
    html = html.replace("{{MESSAGE}}",       message)
    html = html.replace("{{SCHEDULE_INFO}}", schedule_info)
    return html
 
def parse_query(qs):
    """Parse a URL query string into a dict.  e.g. 'a=1&b=2' → {'a':'1','b':'2'}"""
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params
 
def url_decode(s):
    """Minimal URL decoder (handles + and %XX)."""
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
 
# ── Web server ───────────────────────────────────────────────────────────────
async def process_requests(reader, writer):
    """
    Async callback invoked for every incoming HTTP connection.
    Handles:
      GET /                    → serve the main page
      GET /set?start_time=&duration=   → save schedule
      GET /command?cmd=EMERGENCY_UNLOCK → emergency unlock
    """
    try:
        request_line = await reader.readline()
        request_line = str(request_line, "utf-8").strip()
        print("REQ:", request_line)
 
        # Drain remaining headers
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", b"\n"):
                break
 
        if not request_line or "favicon" in request_line:
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return
 
        # Parse method and path
        parts = request_line.split()
        if len(parts) < 2:
            await writer.wait_closed()
            return
 
        path = parts[1]                           # e.g.  /set?start_time=08%3A00&duration=30
        if "?" in path:
            route, qs = path.split("?", 1)
        else:
            route, qs = path, ""
 
        params = parse_query(qs)
        message      = ""
        schedule_info = state["schedule_info"]
 
        # ── Route: save schedule ──────────────────────────────────────────
        if route == "/set":
            st  = params.get("start_time", "").strip()
            dur = params.get("duration",   "").strip()
            if st and dur and dur.isdigit() and int(dur) > 0:
                state["start_time"] = st
                state["duration"]   = int(dur)
                message = f"Schedule saved! Starting at {st} for {dur} min."
                schedule_info = message
                state["schedule_info"] = schedule_info
                state["message"] = message
                print("Schedule set:", st, dur)
            else:
                message = "Invalid input. Please enter a valid time and duration."
 
        # ── Route: command ────────────────────────────────────────────────
        elif route == "/command":
            cmd = params.get("cmd", "")
            if cmd == "EMERGENCY_UNLOCK":
                state["emergency_unlock"] = True
                message = "Emergency unlock triggered!"
                state["message"] = message
                print("EMERGENCY UNLOCK requested via web")
            elif cmd == "LOCK":
                # Manual "phone placed" confirmation from web (optional)
                message = "Manual lock command received."
            else:
                message = f"Unknown command: {cmd}"
 
        # ── Route: root / anything else ───────────────────────────────────
        else:
            message = state["message"]
 
        html     = render_page(message, schedule_info)
        response = (
            "HTTP/1.0 200 OK\r\n"
            "Content-Type: text/html\r\n"
            f"Content-Length: {len(html)}\r\n"
            "\r\n"
            + html
        )
        writer.write(response.encode())
        await writer.drain()
 
    except Exception as e:
        print("Request error:", e)
    finally:
        await writer.wait_closed()
        print("Client disconnected")
 
# ── LCD helpers ───────────────────────────────────────────────────────────────
def lcd_show(line1="", line2=""):
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr(line1[:16])
    lcd.move_to(0, 1)
    lcd.putstr(line2[:16])
 
# ── Speaker ───────────────────────────────────────────────────────────────────
def mount_sd():
    try:
        cs  = machine.Pin(17, machine.Pin.OUT)
        spi = machine.SPI(0, baudrate=1_000_000,
                          sck=machine.Pin(18), mosi=machine.Pin(19), miso=machine.Pin(16))
        sd = sdcard.SDCard(spi, cs)
        vfs.mount(vfs.VfsFat(sd), "/sd") #type: ignore
        print("SD mounted:", os.listdir("/sd"))
        return True
    except Exception as e:
        print("SD mount failed:", e)
        return False
 
def play_alarm():
    """Blocking WAV playback — call only from within an async task using run_in_executor
       or accept that it blocks briefly. For a Pico, a short beep is fine to block on."""
    pin = machine.PWM(machine.Pin(22))
    pin.freq(20_000)
    try:
        with open("/sd/motivation2.wav", "rb") as f:
            hdr = f.read(44)
            if len(hdr) < 44:
                return
            sr = int.from_bytes(hdr[24:28], "little")
            delay = int(500_000 / sr)
            while True:
                chunk = f.read(256)
                if not chunk:
                    break
                for byte in chunk:
                    pin.duty_u16(byte * 257)
                    utime.sleep_us(delay)
    except Exception as e:
        print("Speaker error:", e)
    finally:
        pin.duty_u16(0)
        pin.deinit()
 
# ── Box hardware helpers ──────────────────────────────────────────────────────
def close_box():
    print("Closing box...")
    stepper.step(STEPS_TO_CLOSE)
    lin_act.forward(100)
    time.sleep(2)
    lin_act.stop()
 
def open_box():
    print("Opening box...")
    lin_act.backwards(100)
    time.sleep(2)
    lin_act.stop()
    stepper.step(-STEPS_TO_CLOSE)
 
# ── Time helpers ──────────────────────────────────────────────────────────────
def current_hhmm():
    """Return current local time as 'HH:MM' string (no NTP — AP mode has no internet)."""
    t = time.localtime()
    return f"{t[3]:02d}:{t[4]:02d}"
 
def current_hhmmss():
    t = time.localtime()
    return f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
 
# ── LCD task ──────────────────────────────────────────────────────────────────
async def lcd_task():
    """Continuously updates the LCD display."""
    while True:
        if state["session_active"]:
            # Show current time + session running indicator
            lcd_show(current_hhmmss(), "Session active")
        else:
            # Show SSID/password so the user can connect
            lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")
        await asyncio.sleep(1)
 
# ── Weight / session management task ─────────────────────────────────────────
async def weight_and_session_task():
    """
    Core logic task — runs forever alongside the web server.
 
    State machine:
      1. Idle           : wait for scheduled start time
      2. Alarm          : play alarm until phone detected
      3. Session active : monitor weight; open on timeout or emergency unlock
      4. Done           : return to idle
    """
    sd_ok = mount_sd()
 
    while True:
        # ── 1. Wait for scheduled start time ─────────────────────────────
        if not state["session_active"]:
            if state["start_time"] and state["duration"]:
                now = current_hhmm()
                if now == state["start_time"]:
                    print("Study session starting!")
                    state["session_active"] = True
                    state["phone_in_box"]   = False
                    state["emergency_unlock"] = False
 
                    # ── 2. Play alarm until phone detected ────────────────
                    print("Playing alarm – put your phone in the box!")
                    lcd_show("PUT PHONE IN BOX", "")
                    while not state["phone_in_box"]:
                        # Check weight
                        try:
                            weight = hx711.get_units()
                            print(f"Weight: {weight:.1f} g")
                            if weight >= THRESHOLD:
                                state["phone_in_box"] = True
                                print("Phone detected!")
                                break
                        except Exception as e:
                            print("HX711 read error:", e)
 
                        if sd_ok:
                            play_alarm()        # blocking but short per chunk
                        await asyncio.sleep(0.1)
 
                    # ── 3. Close & lock box ───────────────────────────────
                    close_box()
                    session_end = time.time() + state["duration"] * 60
                    lcd_show("Session locked", f"{state['duration']} min")
 
                    # ── 4. Monitor during session ─────────────────────────
                    while time.time() < session_end:
                        if state["emergency_unlock"]:
                            print("Emergency unlock!")
                            lcd_show("EMERGENCY UNLOCK", "Opening...")
                            break
 
                        remaining = int((session_end - time.time()) / 60)
                        lcd_show(current_hhmmss(), f"Left: {remaining}min")
                        await asyncio.sleep(5)
 
                    # ── 5. Open box & reset ───────────────────────────────
                    open_box()
                    state["session_active"]   = False
                    state["phone_in_box"]     = False
                    state["emergency_unlock"] = False
                    state["start_time"]       = None
                    state["duration"]         = None
                    state["message"]          = "Session complete! Great work."
                    state["schedule_info"]    = ""
                    print("Session complete.")
 
        await asyncio.sleep(5)   # check every 5 s when idle
 
# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    ip = create_ap(SSID, PASSWORD)
    if not ip:
        print("Cannot start without AP. Halting.")
        return
 
    lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")
 
    # Start async web server (callback-based — no manual accept() loop needed)
    server = await asyncio.start_server(process_requests, "0.0.0.0", 80)
    print(f"Web server listening on http://{ip}/")
 
    # Run all tasks concurrently
    await asyncio.gather(
        lcd_task(),
        weight_and_session_task(),
    )
    # `server` runs in the background automatically once awaited above
 
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
    except Exception as e:
        print("Fatal error:", e)
















