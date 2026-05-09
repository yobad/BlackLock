"""
================================================================================
 BlackLock — main firmware
================================================================================
 Project : BlackLock — a phone-lock box for focused study sessions
 Authors : The BlackLock Team, composed of Jérôme Poupart, Boyang Zhang, and Hyeonwoo Ahn
 Date    : May 2, 2026
 Notes   : This file was generated with substantial help from Claude.ai, which based some of his code
           by code files made by Jérôme Poupart and Boyang Zhang. The
           conversation that produced it can be found at
           https://claude.ai/share/ccf46a7f-43a0-4672-b01e-1f0960541a3e

 What it does (high level):
   1. Brings up a Wi-Fi access point so a phone or laptop can connect directly
      to the Pico (no router required).
   2. Serves a small website at 192.168.4.1 where the user schedules a study
      session, sets a default duration, and triggers an emergency unlock.
   3. Reads a load cell so it knows when the user has put their phone in.
   4. Drives a stepper motor that closes / opens the lid.
   5. Plays a motivational alarm out of a PWM speaker until the phone goes in.
   6. Shows status on a 16x2 I²C LCD.
   7. Listens to a physical emergency-unlock button on a GPIO.

 Architecture:
   Three async tasks run concurrently under MicroPython's asyncio:
     • web server (process_requests)        ← HTTP / settings
     • lcd_task                             ← idle & schedule LCD updates
     • weight_and_session_task              ← the "brain" — state machine
     • emergency_button_task                ← physical button polling
   They communicate ONLY via the global `state` dict, defined below. Tasks
   read/write its keys; nobody calls anybody else directly. This keeps the
   tasks decoupled and easy to reason about.
================================================================================
"""

# ─── Standard / hardware imports ────────────────────────────────────────────
import asyncio                       # Cooperative multitasking
import network                       # Wi-Fi access point
import time                          # localtime() reads the Pico's RTC
import utime                         # ticks_ms()/ticks_us() — finer than time
import random                        # Pick a random alarm WAV
import os, sdcard, vfs               # SD card mount/listing for audio files
import machine                       # RTC, raw PWM/SPI/Pin access
from machine import Pin, PWM, I2C    # GPIO, PWM speaker, I²C bus for LCD

# ─── Project libraries ──────────────────────────────────────────────────────
from pico_i2c_lcd import I2cLcd      # 16x2 LCD over I²C backpack
from hx711_gpio import HX711         # Load cell amplifier driver
import motor                         # Stepper (uln2003-style) driver        # H-bridge DC motor (kept for future use)


# ============================================================================
#  CONFIGURATION
# ============================================================================
#  Anything that might reasonably need to change lives in this section. There
#  should be no "magic numbers" sprinkled deeper in the file — if you spot one,
#  promote it up here.
# ============================================================================

# ── Wi-Fi access point ──────────────────────────────────────────────────────
SSID         = "BlackLock"
PASSWORD     = "BlackLock2"
WEB_PORT     = 80                    # HTTP listens on this port
AP_TIMEOUT_S = 10                    # Max time to wait for AP to come up

# ── Audio (SD card + PWM speaker) ───────────────────────────────────────────
SPEAKER_PIN  = 22                    # GPIO connected to PAM8403/etc. amp input
SPEAKER_FREQ_HZ = 20000             # PWM carrier frequency (above human hearing)
WAV_DIR      = "/sd"                 # All WAVs live on the SD card
WAV_FILES    = [
    "/sd/motivation2.wav",
    "/sd/Sneaky.wav",
    "/sd/EvaSong2.wav",
    "/sd/Piggi.wav",
    "/sd/banana2.wav",
    "/sd/RickRoll.wav",
    "/sd/Barbie.wav",
    "/sd/Way.wav",
    "/sd/Times.wav",
    "/sd/amongus.wav",
]
# SD card SPI bus pins
SD_SPI_BUS    = 0
SD_CS_PIN     = 17
SD_SCK_PIN    = 18
SD_MOSI_PIN   = 19
SD_MISO_PIN   = 16
SD_BAUDRATE   = 1000000            # 1 MHz — slow but reliable on long wires

# ── Speaker / playback tuning ───────────────────────────────────────────────
WAV_HEADER_BYTES   = 44              # Standard WAV header is 44 bytes
WAV_HEADER_PROBE   = 100             # Read this many to also peek at format chunk
AUDIO_CHUNK_BYTES  = 100             # Smaller chunks → cleaner output (vs 256)
STOP_CHECK_EVERY   = 100             # How many chunks between stop_fn calls.
                                     # Calling stop_fn every chunk caused audio
                                     # glitches because hx711.get_units() can
                                     # block ~100 ms. ~500 ms between checks
                                     # keeps audio smooth and still responds to
                                     # phone placement well within 2 s.

# ── Load cell (HX711) ───────────────────────────────────────────────────────
HX711_DATA_PIN_NUM  = 6
HX711_CLOCK_PIN_NUM = 5
HX711_SCALE_FACTOR  = 1138.6597      # Calibrated value to convert raw → grams
PHONE_THRESHOLD_G   = 150            # Above this weight = phone is on the sensor

# ── Stepper motor (lid drive) ───────────────────────────────────────────────
STEP_IN1, STEP_IN2 = 14, 12
STEP_IN3, STEP_IN4 = 13, 15
STEPS_TO_CLOSE     = 1700            # Empirical: enough rotation to close lid
                                     # Sign convention: negative = close,
                                     # positive = open. Flip if your wiring is
                                     # the opposite (see close_box / open_box).

# ── LCD (16x2 I²C) ──────────────────────────────────────────────────────────
LCD_I2C_BUS   = 0
LCD_SDA_PIN   = 0
LCD_SCL_PIN   = 1
LCD_I2C_FREQ  = 10000               # Slow I²C clock (10 kHz). Long jumper
                                     # wires get noisy at higher rates.
LCD_I2C_ADDR  = 0x27                 # Common backpack default; some boards 0x3F
LCD_ROWS      = 2
LCD_COLS      = 16

# ── Emergency button (physical) ─────────────────────────────────────────────
EMERGENCY_BTN_PIN_NUM = 10
BUTTON_POLL_S         = 0.02         # 20 ms — feels instant to a finger press

# ── Session timing ──────────────────────────────────────────────────────────
DEFAULT_DURATION_MIN = 1             # Used when phone placed without picking
                                     # a duration via the website.
GRACE_SECONDS        = 30            # After session ends, how long the box
                                     # stays open for the user to grab the
                                     # phone before re-locking.
GRACE_DEBOUNCE       = 3             # Consecutive below-threshold readings
                                     # required to declare "phone removed".
                                     # Stepper vibration causes spurious low
                                     # readings right after open_box(); this
                                     # debounce prevents premature exit.
GRACE_SETTLE_SEC     = 1             # Wait this long after open_box() before
                                     # we even start polling, so the lid stops
                                     # vibrating.
LCD_REFRESH_SEC      = 3             # How often to redraw the LCD during the
                                     # countdown. The timing loop still ticks
                                     # every second; this just throttles the
                                     # actual lcd.clear()+putstr to reduce
                                     # flicker if needed.
SESSION_POLL_S       = 1             # How often the main loop checks for new
                                     # triggers (clock match or phone placed).

# ── Emergency-unlock alarm (after the box opens) ────────────────────────────
EMERGENCY_MIN_MS = 20000            # Plays for AT LEAST this long no matter
                                     # what — annoying enough to discourage
                                     # casual presses.
EMERGENCY_MAX_MS = 60000            # Hard cap — speaker overheats above this.

# ── Idle LCD display ────────────────────────────────────────────────────────
LCD_IDLE_REFRESH_S = 2               # Alternate SSID/PW <-> SSID/IP every Ns

# ── Misc ────────────────────────────────────────────────────────────────────
DEBUG_LOOP_INTERVAL = 10             # Print [idle] debug line every N polls
RTC_DEFAULT_DATE    = (2026, 1, 1)   # (year, month, day) used when only the
                                     # time-of-day is sent from the browser.

# Bytes-to-PWM: each WAV byte (0–255) becomes a 16-bit PWM duty (0–65535).
# 257 = 65535 / 255 (no rounding needed).
PWM_BYTE_TO_DUTY_SCALE = 257


# ============================================================================
#  HARDWARE INITIALIZATION
# ============================================================================
#  Each block sets up one peripheral and stores the handle in a top-level
#  variable that the rest of the file uses. Order matters: I²C must come up
#  before the LCD, the HX711 must be tared on a known-empty load cell, etc.
# ============================================================================

# ── Load cell ───────────────────────────────────────────────────────────────
hx711_data  = Pin(HX711_DATA_PIN_NUM,  Pin.IN,  Pin.PULL_DOWN)
hx711_clock = Pin(HX711_CLOCK_PIN_NUM, Pin.OUT)
hx711 = HX711(hx711_clock, hx711_data)
hx711.tare()                          # Zero out — make sure box is empty at boot
hx711.set_scale(HX711_SCALE_FACTOR)

# ── Stepper motor ───────────────────────────────────────────────────────────
stepper = motor.FullStepMotor.frompins(STEP_IN1, STEP_IN2, STEP_IN3, STEP_IN4)

# ── LCD over I²C ────────────────────────────────────────────────────────────
i2c = I2C(LCD_I2C_BUS, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN), freq=LCD_I2C_FREQ)
lcd = I2cLcd(i2c, LCD_I2C_ADDR, LCD_ROWS, LCD_COLS)

# ── Emergency button ────────────────────────────────────────────────────────
# NOTE: this button uses an external pull-down (or active-high wiring) such
# that value() == 1 means PRESSED. If you swap to internal pull-up, change
# every `button.value() == 1` check below to `== 0`.
button = Pin(EMERGENCY_BTN_PIN_NUM, Pin.IN)


# ============================================================================
#  SHARED STATE
# ============================================================================
#  All inter-task communication goes through this dict. Tasks never call each
#  other directly — they just set keys and let other tasks notice the change.
#  Each key is documented inline so it's obvious who writes it and who reads.
# ============================================================================
state = {
    # Schedule (set by the website)
    "start_time":            None,   # str "HH:MM" — scheduled session start
    "duration":              None,   # int — minutes for the next session
    "default_duration":      DEFAULT_DURATION_MIN,
                                     # int — fallback when user starts a
                                     # session without setting a duration

    # Live session flags
    "session_active":        False,  # True from session start through end
    "phone_in_box":          False,  # Latched True once weight crosses threshold
    "emergency_unlock":      False,  # Set by web /command or physical button

    # Pause/resume (emergency-unlock saves the remaining time so the user can
    # resume the same session by putting the phone back in)
    "paused_remaining_sec":  None,

    # Web UI feedback
    "message":               "",     # Status message rendered on the page
    "schedule_info":         "",     # "Schedule saved!" confirmation line
    "ip":                    None,   # Pico's AP-side IP, displayed on LCD
}


# ============================================================================
#  SMALL HELPERS
# ============================================================================

def lcd_show(line1="", line2=""):
    """
    Write two lines to the LCD without using lcd.clear().

    Why no clear: the I²C LCD library's clear() occasionally leaves the
    cursor in a stale position, which makes only some character cells get
    overwritten next time — visible as digits "sticking" between updates.
    Padding each line to the LCD width (16 chars) explicitly forces every
    cell to be (re)written cleanly. Bonus: skipping the slow ~1.6 ms clear()
    instruction makes refreshes faster too.
    """
    line1 = (line1 + " " * LCD_COLS)[:LCD_COLS]
    line2 = (line2 + " " * LCD_COLS)[:LCD_COLS]
    lcd.move_to(0, 0)
    lcd.putstr(line1)
    lcd.move_to(0, 1)
    lcd.putstr(line2)


def current_hhmm():
    """Return the Pico's wall-clock as 'HH:MM'."""
    t = time.localtime()
    return f"{t[3]:02d}:{t[4]:02d}"


def current_hhmmss():
    """Return the Pico's wall-clock as 'HH:MM:SS'."""
    t = time.localtime()
    return f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"


def url_decode(s):
    """
    Decode a URL-encoded string. e.g. '08%3A00' → '08:00'.

    We hand-roll this because urllib isn't available in MicroPython on the
    Pico W's stripped-down standard library.
    """
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


def parse_query(qs):
    """Parse a URL query string into a dict. e.g. 'a=1&b=2' → {'a':'1','b':'2'}."""
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params


# ============================================================================
#  HARDWARE FUNCTIONS — SD CARD, AUDIO, MOTOR
# ============================================================================

def mount_sd():
    """
    Mount the SD card at /sd. Returns True on success.

    Called once at task startup. If it fails, audio playback is silently
    skipped (the rest of the system still works without sound).
    """
    try:
        cs  = Pin(SD_CS_PIN, Pin.OUT)
        spi = machine.SPI(SD_SPI_BUS,
                          baudrate=SD_BAUDRATE,
                          sck=Pin(SD_SCK_PIN),
                          mosi=Pin(SD_MOSI_PIN),
                          miso=Pin(SD_MISO_PIN))
        sd  = sdcard.SDCard(spi, cs)
        vfs.mount(vfs.VfsFat(sd), WAV_DIR)  # type: ignore
        print("SD mounted:", os.listdir(WAV_DIR))
        return True
    except Exception as e:
        print("SD mount failed:", e)
        return False


def play_alarm(stop_fn, wavfile=None):
    """
    Play a WAV file out the PWM speaker, with a stoppable interrupt point.

    Parameters
    ----------
    stop_fn : callable -> bool
        Called every STOP_CHECK_EVERY chunks. If it returns True, playback
        stops cleanly between chunks. This is how the weight sensor and
        emergency button can interrupt a long sound mid-way.
    wavfile : str or None
        Path to a WAV file. If None, picks one at random from WAV_FILES.

    Returns
    -------
    bool : True if stopped early via stop_fn, False if the file ran to EOF.
    """
    pin = PWM(Pin(SPEAKER_PIN))
    pin.freq(SPEAKER_FREQ_HZ)
    stopped_early = False

    try:
        if wavfile is None:
            wavfile = random.choice(WAV_FILES) #type: ignore
        with open(wavfile, "rb") as f: #type: ignore
            # Read the WAV header. 44 is the standard size; we read 100 to
            # ensure we get past any optional format-chunk extras.
            hdr = f.read(WAV_HEADER_PROBE)
            if (len(hdr) < WAV_HEADER_BYTES
                    or hdr[0:4]  != b"RIFF"
                    or hdr[8:12] != b"WAVE"):
                print("Not a valid WAV file:", wavfile)
                return False

            # Sample rate is in bytes 24..28 of the header, little-endian.
            sr = int.from_bytes(hdr[24:28], "little")
            # Each sample needs `delay_us` of dwell time on the PWM pin.
            # 500_000 / sr is half the sample period in µs (the byte-loop
            # below does ONE sleep per byte, not per sample, so this
            # heuristic compensates).
            delay_us = int(500_000 / sr)
            print("sample_rate", sr, "delay_us", delay_us)

            chunk_count = 0
            while True:
                # Throttled stop-check — see STOP_CHECK_EVERY rationale above.
                if chunk_count % STOP_CHECK_EVERY == 0 and stop_fn():
                    stopped_early = True
                    break
                chunk = f.read(AUDIO_CHUNK_BYTES)
                if not chunk:
                    break
                for byte in chunk:
                    pin.duty_u16(byte * PWM_BYTE_TO_DUTY_SCALE)
                    utime.sleep_us(delay_us)
                chunk_count += 1
    except Exception as e:
        print("Speaker error:", e)
    finally:
        pin.duty_u16(0)
        pin.deinit()
    return stopped_early


def close_box():
    """Drive the stepper to fully close the lid. Negative direction = close."""
    print("Closing box...")
    stepper.step(-STEPS_TO_CLOSE)


def open_box():
    """Drive the stepper to fully open the lid. Positive direction = open."""
    print("Opening box...")
    stepper.step(STEPS_TO_CLOSE)


# ============================================================================
#  WI-FI ACCESS POINT
# ============================================================================

def create_ap(ssid, password):
    """
    Bring up the Pico W as a Wi-Fi access point. Blocks up to AP_TIMEOUT_S
    seconds. Returns the AP's IP on success, or None on failure.
    """
    ap = network.WLAN(network.AP_IF)
    ap.config(ssid=ssid, password=password)
    ap.active(True)

    # Spin until the AP reports active (or timeout).
    timeout = AP_TIMEOUT_S
    while not ap.active() and timeout > 0:
        time.sleep(0.5)
        timeout -= 1

    if ap.active():
        ip, _ = ap.ipconfig('addr4')
        print(f"AP ready  →  SSID: {ssid}  PW: {password}  IP: {ip}")
        return ip

    print("AP failed to start")
    return None


# ============================================================================
#  WEB PAGE RENDERING
# ============================================================================

def render_page(message="", schedule_info=""):
    """
    Read index.html from flash, substitute the {{PLACEHOLDERS}} from `state`,
    and inject a small JavaScript snippet that periodically pushes the
    browser's clock to /settime so the Pico's RTC stays correct.
    """
    start = state["start_time"] or ""
    dur   = str(state["duration"]) if state["duration"] else ""

    try:
        with open("index.html", "r") as f:
            html = f.read()
    except OSError:
        return "<h1>Error: index.html not found</h1>"

    # Template substitutions — keep these in sync with index.html.
    html = html.replace("{{START_TIME}}",       start)
    html = html.replace("{{DURATION}}",         dur)
    html = html.replace("{{MESSAGE}}",          message)
    html = html.replace("{{SCHEDULE_INFO}}",    schedule_info)
    html = html.replace("{{DEFAULT_DURATION}}", str(state["default_duration"]))

    # Time-sync script. Runs once on page load and again every 10 s while
    # the page stays open. Without this the Pico's RTC defaults to year-2000
    # and the scheduled-start comparison never matches.
    sync_script = (
        "<script>"
        "(function(){"
        "function sync(){"
        "var d=new Date();"
        "var hh=String(d.getHours()).padStart(2,'0');"
        "var mm=String(d.getMinutes()).padStart(2,'0');"
        "fetch('/settime?hhmm='+hh+':'+mm).catch(function(){});"
        "}"
        "sync();"
        "setInterval(sync,10000);"
        "})();"
        "</script>"
    )
    if "</body>" in html:
        html = html.replace("</body>", sync_script + "</body>")
    else:
        html = html + sync_script

    return html


# ============================================================================
#  WEB SERVER
# ============================================================================
#  Routes:
#    GET /                                — main page
#    GET /set?start_time=HH:MM&duration=N — set schedule
#    GET /set?default_duration=N          — change default-duration only
#    GET /settime?hhmm=HH:MM              — sync RTC from browser
#    GET /command?cmd=EMERGENCY_UNLOCK    — trigger emergency unlock
# ============================================================================

async def process_requests(reader, writer):
    """asyncio HTTP request handler. One coroutine per incoming connection."""
    try:
        # ── Parse request line ──────────────────────────────────────────
        request_line = await reader.readline()
        request_line = str(request_line, "utf-8").strip()
        print("REQ:", request_line)

        # Drain headers until empty line.
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", b"\n"):
                break

        # Browsers always ask for /favicon.ico; reply 204 so they stop asking.
        if not request_line or "favicon" in request_line:
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return

        parts = request_line.split()
        if len(parts) < 2:
            await writer.wait_closed()
            return

        # Split path from query string.
        path = parts[1]
        if "?" in path:
            route, qs = path.split("?", 1)
        else:
            route, qs = path, ""

        params        = parse_query(qs)
        message       = ""
        schedule_info = state["schedule_info"]

        # ── /set : update schedule and/or default duration ──────────────
        if route == "/set":
            st  = params.get("start_time", "").strip()
            dur = params.get("duration",   "").strip()

            # Optional default-duration override (sent by the Apply button).
            default_dur = params.get("default_duration", "").strip()
            if default_dur and default_dur.isdigit() and int(default_dur) > 0:
                state["default_duration"] = int(default_dur)
                print(f"Default duration updated → {state['default_duration']} min")

            if st and dur and dur.isdigit() and int(dur) > 0:
                state["start_time"] = st
                state["duration"]   = int(dur)
                message = f"Schedule saved! Starting at {st} for {dur} min."
                schedule_info = message
                state["schedule_info"] = schedule_info
                state["message"]       = message
                print("Schedule set:", st, dur)
            elif st or dur:
                # User filled in one schedule field but not the other.
                message = "Invalid input. Please enter a valid time and duration."
            elif default_dur:
                # Only default_duration was submitted (Apply button) — confirm.
                message = f"Default session length set to {state['default_duration']} min."
                state["message"] = message
            # else: empty /set request → leave message blank.

        # ── /settime : RTC sync from the browser ────────────────────────
        elif route == "/settime":
            hhmm = params.get("hhmm", "").strip()
            if hhmm and ":" in hhmm:
                try:
                    h, m = hhmm.split(":")
                    y, mo, d = RTC_DEFAULT_DATE
                    machine.RTC().datetime((y, mo, d, 0, int(h), int(m), 0, 0))
                    print(f"RTC synced from browser: {hhmm}")
                except Exception as e:
                    print("RTC set error:", e)
            # /settime is called from JavaScript and the response body is
            # discarded — return the smallest valid response.
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return

        # ── /command : emergency-unlock from the web button ─────────────
        elif route == "/command":
            cmd = params.get("cmd", "")
            if cmd == "EMERGENCY_UNLOCK":
                state["emergency_unlock"] = True
                message = "Emergency unlock triggered!"
                state["message"] = message
                print("EMERGENCY UNLOCK requested via web")
            else:
                message = f"Unknown command: {cmd}"

        # ── /  (or anything else) : just render the current state ───────
        else:
            message = state["message"]

        # ── Render and send the page ────────────────────────────────────
        html = render_page(message, schedule_info)
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


# ============================================================================
#  ASYNC TASK : LCD display while idle
# ============================================================================
#  Owns the LCD whenever no session is running. During an active session the
#  weight_and_session_task takes over the LCD (countdown, "Box opening", etc.)
#  and this task quietly steps aside.
# ============================================================================

async def lcd_task():
    show_ip_next = False
    while True:
        if state["session_active"]:
            # Active session — let the session task drive the LCD.
            pass

        elif state["start_time"] and state["duration"]:
            # A schedule has been received but the session hasn't started.
            lcd_show(f"Start: {state['start_time']}",
                     f"Duration: {state['duration']} min")

        else:
            # Truly idle — alternate between Wi-Fi credentials and IP so the
            # user always sees what to connect to and where to point a browser.
            if show_ip_next:
                lcd_show(f"SSID:{SSID}", f"IP: {state['ip']}")
            else:
                lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")
            show_ip_next = not show_ip_next

        await asyncio.sleep(LCD_IDLE_REFRESH_S)


# ============================================================================
#  ASYNC TASK : the brain — weight monitoring + session state machine
# ============================================================================
#  This is the largest and most important task. Stages:
#
#    (A) IDLE
#        Wait for either:
#          • the scheduled start time to arrive, OR
#          • the user to drop their phone on the sensor (weight trigger).
#
#    (B) ALARM
#        Box is open. Loop random WAVs until the phone is detected (or
#        emergency unlock fires).
#
#    (C) CLOSE + COUNTDOWN
#        close_box() runs the stepper. LCD shows the MM:SS countdown. If
#        emergency unlock fires we save the remaining seconds for resume.
#
#    (D) OPEN
#        At end of countdown OR on emergency unlock: open_box(). If it was
#        an emergency unlock, play a guilt-trip alarm for ≥20 s; if the
#        phone is placed back during the alarm, we re-close and resume the
#        countdown.
#
#    (E) GRACE
#        Box stays open for GRACE_SECONDS so the user can grab the phone.
#        If the phone is still on the sensor when grace expires, the box
#        re-locks for another DEFAULT_DURATION session (anti-procrastination).
# ============================================================================

async def weight_and_session_task():
    sd_ok        = mount_sd()
    loop_counter = 0

    while True:
        # ── Periodic debug print ───────────────────────────────────────
        # Every DEBUG_LOOP_INTERVAL polls (~10 s with SESSION_POLL_S=1)
        # log the comparison values so RTC-sync issues are easy to spot.
        loop_counter += 1
        if loop_counter % DEBUG_LOOP_INTERVAL == 0 and state["start_time"]:
            print(f"[idle] Pico clock={current_hhmm()}  "
                  f"waiting for={state['start_time']}  "
                  f"duration={state['duration']}")

        # ── (A) IDLE — decide whether to start a session ───────────────
        if not state["session_active"]:
            try:
                w_now = hx711.get_units()
            except Exception:
                w_now = 0.0
            phone_already_there = w_now >= PHONE_THRESHOLD_G

            has_schedule   = state["start_time"] and state["duration"]
            time_trigger   = has_schedule and current_hhmm() == state["start_time"]
            weight_trigger = phone_already_there

            if time_trigger or weight_trigger:
                # If duration is missing (phone-trigger with no website
                # input) fall back to default OR a paused remaining time.
                if not state["duration"]:
                    if state["paused_remaining_sec"]:
                        # Resume from a previously emergency-unlocked session.
                        state["duration"] = max(
                            1,
                            (state["paused_remaining_sec"] + 59) // 60
                        )
                        # NOTE: we don't clear paused_remaining_sec here —
                        # the inner loop's "Pick the countdown length" block
                        # consumes it so seconds-precision is preserved.
                        print(f"Resuming previous session — "
                              f"{state['duration']} min remaining")
                    else:
                        state["duration"] = state["default_duration"]
                        print(f"No duration set — defaulting to "
                              f"{state['default_duration']} min")

                print(f"Starting session  "
                      f"(time={time_trigger}, weight={weight_trigger})")
                state["session_active"]   = True
                state["phone_in_box"]     = phone_already_there
                state["emergency_unlock"] = False

                # ── (B) ALARM — only if phone isn't already on the sensor
                if not state["phone_in_box"]:
                    print("Playing alarm – put your phone in the box!")
                    lcd_show("PUT PHONE IN BOX", "")

                    def phone_or_unlock():
                        """
                        Stop callback for play_alarm. Returns True when
                        either the phone is detected or the emergency
                        button is pressed — both reasons to stop the WAV.
                        """
                        if state["emergency_unlock"]:
                            return True
                        try:
                            w = hx711.get_units()
                            if w >= PHONE_THRESHOLD_G:
                                state["phone_in_box"] = True
                                print(f"Phone detected at {w:.1f} g")
                                return True
                        except Exception as e:
                            print("HX711 read error:", e)
                        return False

                    # Loop random WAVs until the phone goes in (or unlock).
                    while (not state["phone_in_box"]
                           and not state["emergency_unlock"]):
                        if sd_ok:
                            play_alarm(phone_or_unlock)
                        else:
                            phone_or_unlock()
                        await asyncio.sleep(0.1)

                    # Emergency unlock during alarm → cancel cleanly.
                    if state["emergency_unlock"]:
                        print("Emergency unlock during alarm — "
                              "aborting session")
                        lcd_show("EMERGENCY UNLOCK", "Aborted")
                        state["session_active"]   = False
                        state["phone_in_box"]     = False
                        state["emergency_unlock"] = False
                        state["start_time"]       = None
                        state["duration"]         = None
                        state["message"]          = "Session cancelled."
                        state["schedule_info"]    = ""
                        await asyncio.sleep(5)
                        continue
                else:
                    print("Phone already on sensor — skipping alarm, "
                          "closing now")

                # ── (C) CLOSE + start countdown ────────────────────────
                close_box()
                lcd_show("Session locked", f"{state['duration']} min")

                # The inner `while True` loop lets us re-enter the countdown
                # phase if the user leaves their phone in the box past the
                # grace period. In that case we close again for another
                # DEFAULT_DURATION session.
                phone_removed = False

                while True:
                    # Decide the countdown length. Resume from a paused
                    # session if one exists, otherwise use full duration.
                    if state["paused_remaining_sec"]:
                        total_sec = state["paused_remaining_sec"]
                        state["paused_remaining_sec"] = None
                        print(f"Resuming paused session — {total_sec}s left")
                    else:
                        total_sec = state["duration"] * 60

                    # ── Countdown ──────────────────────────────────────
                    # Counter-based (range) instead of timestamp-based so
                    # every value from total_sec down to 00:00 actually
                    # gets displayed. Tick-anchored sleep keeps the wall
                    # clock and "Left:" counter in lockstep regardless of
                    # how long each lcd_show write takes.
                    aborted         = False
                    countdown_start = time.ticks_ms()
                    mins, secs = 0, 0  # initialise so the post-loop redraw works

                    for frame in range(total_sec):
                        remaining_sec = total_sec - frame

                        if state["emergency_unlock"]:
                            # Save remaining seconds so the user can resume
                            # by placing the phone back later.
                            state["paused_remaining_sec"] = remaining_sec
                            print(f"Emergency unlock — "
                                  f"{remaining_sec}s saved for resume")
                            aborted = True
                            break

                        # Throttled LCD update. Timing loop still runs every
                        # second below so the wall clock stays accurate.
                        if frame % LCD_REFRESH_SEC == 0:
                            mins, secs = divmod(remaining_sec, 60)
                            lcd_show(f"Current: {current_hhmm()}",
                                     f"Left: {mins:02d}:{secs:02d}")

                        # Sleep until the next absolute 1-second boundary.
                        target   = time.ticks_add(countdown_start,
                                                  (frame + 1) * 1000)
                        delay_ms = time.ticks_diff(target, time.ticks_ms())
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000)

                    # Show the final 00:00 frame for one beat so the user
                    # actually sees the countdown finish.
                    if not aborted:
                        lcd_show(f"Current: {current_hhmm()}",
                                 f"Left: {mins:02d}:{secs:02d}")
                        await asyncio.sleep(1)

                    # ── (D) OPEN box ───────────────────────────────────
                    if aborted:
                        print("Emergency unlock!")
                        lcd_show("EMERGENCY UNLOCK", "Opening...")
                    else:
                        lcd_show("Box is opening", "Take your phone")
                    open_box()

                    # ── Emergency-unlock alarm + possible resume ───────
                    if aborted:
                        # Play a "do not give up" alarm for at least
                        # EMERGENCY_MIN_MS, looping random WAVs. After the
                        # minimum, stop as soon as the phone goes back on
                        # the sensor. Hard cap at EMERGENCY_MAX_MS so the
                        # speaker doesn't overheat.
                        lcd_show("DO NOT GIVE UP", "Put phone back!")
                        emerg_start = utime.ticks_ms()
                        phone_back  = False

                        def emergency_stop_fn():
                            """
                            Stop callback used during the emergency alarm.
                            Returns True when the alarm should stop. Three
                            stop conditions:
                              1. Hard time cap reached.
                              2. Past the minimum duration AND phone is
                                 back on the sensor.
                              3. (Implicit) callable returns False otherwise.
                            """
                            nonlocal phone_back
                            elapsed = utime.ticks_diff(utime.ticks_ms(),
                                                       emerg_start)
                            if elapsed >= EMERGENCY_MAX_MS:
                                return True
                            if elapsed < EMERGENCY_MIN_MS:
                                return False
                            try:
                                if hx711.get_units() >= PHONE_THRESHOLD_G:
                                    phone_back = True
                                    return True
                            except Exception:
                                pass
                            return False

                        if sd_ok:
                            while not emergency_stop_fn():
                                play_alarm(emergency_stop_fn)
                                # play_alarm's finally block already deinits
                                # the PWM, but rapid re-creation can leave a
                                # residual duty on the speaker. Force-zero
                                # the pin between WAVs so it's truly silent.
                                try:
                                    quiet = PWM(Pin(SPEAKER_PIN))
                                    quiet.duty_u16(0)
                                    quiet.deinit()
                                except Exception:
                                    pass
                                await asyncio.sleep(0)
                        else:
                            # No SD = no audio. Just wait the minimum then
                            # poll for the phone manually.
                            await asyncio.sleep(EMERGENCY_MIN_MS / 1000)
                            while not emergency_stop_fn():
                                await asyncio.sleep(0.2)

                        state["emergency_unlock"] = False

                        # Phone is back AND we have saved time → re-close
                        # and resume the countdown from where we paused.
                        # Skip the grace period entirely.
                        if phone_back and state["paused_remaining_sec"]:
                            print("Phone is back — re-closing and "
                                  "resuming countdown")
                            lcd_show("Resuming session", "Box closing...")
                            close_box()
                            continue   # restart inner loop with paused time

                        # Otherwise: alarm timed out without phone return.
                        # Final load-cell check — if the phone is gone,
                        # end the session immediately rather than going
                        # into the grace period.
                        try:
                            final_w = hx711.get_units()
                        except Exception:
                            final_w = PHONE_THRESHOLD_G  # assume present on error
                        state["paused_remaining_sec"] = None
                        if final_w < PHONE_THRESHOLD_G:
                            print(f"Phone removed during alarm "
                                  f"({final_w:.1f} g) — ending session")
                            lcd_show("Session ended", "Phone removed")
                            phone_removed = True
                            await asyncio.sleep(2)
                            break   # exit inner loop → idle reset

                    # Clear any residual emergency flag.
                    state["emergency_unlock"] = False

                    # ── (E) GRACE PERIOD ───────────────────────────────
                    # Wait a moment for stepper vibration to die down before
                    # we trust load-cell readings (the "phone removed" check
                    # is otherwise fooled by mechanical bounce).
                    await asyncio.sleep(GRACE_SETTLE_SEC)
                    phone_removed = False
                    below_count   = 0
                    grace_start   = time.ticks_ms()

                    for sec_idx in range(GRACE_SECONDS):
                        remaining_sec = GRACE_SECONDS - sec_idx

                        # Box is already open during grace, so emergency
                        # presses have nothing to unlock — absorb them.
                        if state["emergency_unlock"]:
                            state["emergency_unlock"] = False
                            print("Emergency press during grace — "
                                  "box already open, ignoring")

                        try:
                            w = hx711.get_units()
                        except Exception as e:
                            print("HX711 read error:", e)
                            # Treat read errors as "phone still there" so
                            # a flaky sensor can't prematurely end the
                            # session.
                            w = PHONE_THRESHOLD_G

                        if w < PHONE_THRESHOLD_G:
                            below_count += 1
                            if below_count >= GRACE_DEBOUNCE:
                                phone_removed = True
                                print(f"Phone removed ({w:.1f} g) — "
                                      f"session ends")
                                break
                        else:
                            # Reset on any above-threshold sample (we want
                            # CONSECUTIVE below-threshold readings).
                            below_count = 0

                        # Throttle LCD redraws but keep weight polling once
                        # per second.
                        if sec_idx % LCD_REFRESH_SEC == 0:
                            mins, secs = divmod(remaining_sec, 60)
                            lcd_show("Take phone out!",
                                     f"Close: {mins:02d}:{secs:02d}")

                        target   = time.ticks_add(grace_start,
                                                  (sec_idx + 1) * 1000)
                        delay_ms = time.ticks_diff(target, time.ticks_ms())
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000)

                    if phone_removed:
                        break   # exit inner re-lock loop

                    # Phone STILL there after grace → re-lock for another
                    # DEFAULT_DURATION session (anti-procrastination).
                    print(f"Phone not removed — re-locking for "
                          f"{state['default_duration']} min")
                    lcd_show("Phone still in", "Re-locking...")
                    await asyncio.sleep(2)   # let user read the message
                    close_box()
                    state["duration"] = state["default_duration"]
                    # Loop back to the countdown with the fresh duration.

                # ── Reset all state back to idle ────────────────────────
                state["session_active"]       = False
                state["phone_in_box"]         = False
                state["emergency_unlock"]     = False
                state["start_time"]           = None
                state["duration"]             = None
                state["paused_remaining_sec"] = None
                state["message"]              = "Session complete! Great work."
                state["schedule_info"]        = ""
                print("Session complete.")

        # Idle loop tick.
        await asyncio.sleep(SESSION_POLL_S)


# ============================================================================
#  ASYNC TASK : physical emergency button polling
# ============================================================================
#  Sets state["emergency_unlock"] = True when the button is pressed. The
#  weight_and_session_task notices on its next iteration.
# ============================================================================

async def emergency_button_task():
    while True:
        if button.value() == 1:
            state["emergency_unlock"] = True
            state["message"] = "Emergency unlock triggered!"
            print("EMERGENCY UNLOCK requested via physical button")
            # Wait for release before re-arming so a single press = a single
            # event (no auto-repeat while held).
            while button.value() == 1:
                await asyncio.sleep(BUTTON_POLL_S)
        await asyncio.sleep(BUTTON_POLL_S)


# ============================================================================
#  MAIN ENTRY POINT
# ============================================================================

async def main():
    """Bring up the AP, the web server, and all background tasks."""
    state["ip"] = create_ap(SSID, PASSWORD)
    if not state["ip"]:
        print("Cannot start without access point. Halting.")
        return

    lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")

    # Start the async web server. It runs in the background once awaited;
    # we never need to call accept() ourselves.
    server = await asyncio.start_server(process_requests, "0.0.0.0", WEB_PORT)
    print(f"Web server listening on http://{state['ip']}:{WEB_PORT}/")

    # All concurrent tasks run from here. asyncio.gather blocks until any
    # of them returns or raises.
    await asyncio.gather(
        lcd_task(),
        weight_and_session_task(),
        emergency_button_task(),
    )


# Standard "this is the file you ran" guard. Catching KeyboardInterrupt lets
# Thonny / mpremote stop cleanly with Ctrl-C; any other exception is logged.
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
    except Exception as e:
        print("Fatal error:", e)