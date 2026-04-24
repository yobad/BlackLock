"""
Description: This is the main file running the BlackLock. It includes:
wifi hotspot created by Pico, async weight sensor monitoring, async web server,
LCD display updates, and speaker.
Date: Apr 23, 2026
Author: The BlackLock Team 
This file was generated using Claude.ai, we put our own code (html website, speaker, internet and motor files) as 
reference so that the AI could put them all together
Here's a link to the discussion: https://claude.ai/share/ccf46a7f-43a0-4672-b01e-1f0960541a3e

"""
 
#Library imported
import asyncio # For async programming (web server and weight monitoring run concurrently)
import network
import time
from machine import Pin, PWM, I2C
#Pin= controls GPIO pin
#PWM = controls pulse width modulation for motor speed and speaker
#I2C = for LCD communication
from pico_i2c_lcd import I2cLcd # LCD library, let you control an LCD screen over I2C with simple commands
from hx711_gpio import HX711 # Load cell library, provides an interface to read weight data from a load cell using the HX711 amplifier
import motor # Stepper motor library, provides an easy interface to control a stepper motor using GPIO pins
from dcmotor import DCMotor # DC motor library, provides an easy interface to control a DC motor using GPIO pins and PWM for speed control
import machine 
import utime # For low-level hardware control and time functions
import os, sdcard, vfs #for sd file management, used for speaker audio files
 
#----------------------------------------------------------------------------------------------------------------
# Configuration (could be moved to a separate config file, for safety of passwords))
SSID      = "BlackLock"
PASSWORD  = "BlackLock2"
 
# This is the Load cell pin setup fo the weight sensor (adjustable based on our wiring)
DATA_PIN  = Pin(6, Pin.IN, pull=Pin.PULL_DOWN)
CLOCK_PIN = Pin(5, Pin.OUT) # sends clock signal to HX711 to trigger readings
SCALE_F   = 1138.6597 #value to convert raw HX711 readings to grams — tweak after calibration
THRESHOLD = 100         # grams — adjust based on your phone's weight + box setup
DEFAULT_DURATION = 1   # minutes — used when the phone is placed on the sensor
                         # without a duration having been set via the website.
GRACE_SECONDS    = 30   # seconds — after a session ends the box stays open for
                         # this long to let the user grab their phone. If weight
                         # is still detected when the grace period ends, the box
                         # re-locks for another DEFAULT_DURATION session.
GRACE_DEBOUNCE   = 3     # require this many consecutive below-threshold readings
                         # before believing the phone has actually been removed.
                         # Single readings aren't trustworthy: the stepper motor
                         # that just ran open_box() shakes the load cell, which
                         # makes the first couple of HX711 samples dip below the
                         # threshold even when the phone is still sitting there.
GRACE_SETTLE_SEC = 1     # seconds to wait after open_box() before starting to
                         # poll the load cell, so mechanical vibration dies out.
LCD_REFRESH_SEC  = 2     # how often the LCD is redrawn during the countdown
                         # and grace period. The underlying timing loop still
                         # runs every 1 s (for tick-accurate timekeeping and
                         # emergency-unlock checks), but the screen is only
                         # cleared-and-rewritten every N seconds to reduce the
                         # visible flicker caused by lcd.clear(). Raise this if
                         # flicker is still annoying; 1 = update every second.
 
# Stepper motor
STEP_IN1, STEP_IN2, STEP_IN3, STEP_IN4 = 14, 12, 13, 15 # stepper motor pins (adjust to match wiring)
STEPS_TO_CLOSE = 1700 # number of steps to fully close the box — adjust based on stepper motor and gearing
 
 
# LCD pin setup (adjust if using different pins)
#0x27 is the common I2C address for our LCD, if different we can change it here
I2C_ADDR   = 0x27
I2C_NUM_ROWS, I2C_NUM_COLS = 2, 16
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=10000)
lcd = I2cLcd(i2c, I2C_ADDR, I2C_NUM_ROWS, I2C_NUM_COLS)

#------------------------------------------------------------------------------------------------------------------
#THE ASYNC DICTIONARY
# This code is structured around a shared `state` dictionary that all tasks read from and write to.
#  For example, when the web server receives a new schedule,
#  it updates `state["start_time"]` and `state["duration"]` to the new values,
#  and BlackLock reacts to those changes. Pretty impressive technique.
state = {
    "start_time": None,       # "Hours 00:00 Minutes"  string from the web form
    "duration":   None,       # integer, minutes
    "session_active": False,  # Is True while a study session is running
    "phone_in_box":   False,  # Is True once weight sensor detects phone
    "emergency_unlock": False,# Set to True by the web emergency button
    "message":   "",          # Status message shown on the web page
    "schedule_info": "",      # Confirmation of the current schedule is shown on the web page
    "ip": None,               # IP address of the access point
}
#----------------------------------------------------------------------------------------------------------------------
#The purpose of these line of code of to set up the hardware (stepper motor, linear actuator and load cell)
#LOAD CELL SETUP
hx711 = HX711(CLOCK_PIN, DATA_PIN)
hx711.tare()
hx711.set_scale(SCALE_F)
#STEPPER MOTOR SETUP
stepper = motor.FullStepMotor.frompins(STEP_IN1, STEP_IN2, STEP_IN3, STEP_IN4)
#----------------------------------------------------------------------------------------------------------------------
# This code is to setup the hotspot (access point (ap)) for the pico
def create_ap(ssid, password):
    ap = network.WLAN(network.AP_IF) # Create a WLAN object in access point mode, Wlan is a class that provides methods to control the Wi-Fi interface of the Pico, and AP_IF specifies that we want to create an access point
    ap.config(ssid=ssid, password=password) 
    ap.active(True) # Activate the access point
    timeout = 10
    while not ap.active() and timeout > 0: # Wait for the hotspot to become active, with a timeout to avoid infinite loop
        time.sleep(0.5)
        timeout -= 1
    if ap.active(): # If the access point is successfully created, print the details and return the IP address
        ip, _ = ap.ipconfig('addr4')
        print(f"AP ready  →  SSID: {ssid}  PW: {password}  IP: {ip}")
        return ip
    print("AP failed to start") #if ap not active after timeout, print error message and return None
    return None
#----------------------------------------------------------------------------------------------------------------------
# This code is to render the webpage (index.html) with the current state values (from the dictionary seen above).
#  It reads the HTML file, replaces the placeholders with actual values from the state, 
# and returns the final HTML string to be served to the client (us reading what's on the page).
def render_page(message="", schedule_info=""):
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

    # Inject a tiny time-sync script. It runs once on page load and then
    # every 10 seconds while the page stays open, so the Pico's RTC keeps
    # up with the browser's wall clock even if the first fetch fails
    # (caching, transient network error, page opened before JS ready).
    # Injected server-side so index.html itself needs no changes.
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

#----------------------------------------------------------------------------------------------------------------------
# This code is to set up the shared state dictionary that all tasks will read from and write to.
# It includes the current schedule, session status, phone detection status, emergency unlock status, and messages to show on the web page.
#This function turns the URL query string (the part after the ? in a URL) into a dictionary of parameters.
# For example, if the URL is /set?start_time=08%3A00, then the query string is "start_time=08%3A00",
# and this function will return {"start_time": "08:00"} after decoding the URL-encoded characters.
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
 
#This function decodes URL-encoded strings, converting + to space and %XX to the corresponding character.
#In other words, it takes a string like "08%3A00" and turns it into "08:00",
#  which is more human-readable and can be used in our program logic.

#Difference from parse_query is that parse_query splits the query string into 
# key-value pairs and calls url_decode on each key and value, 
# while url_decode just decodes a single URL-encoded string.
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

#----------------------------------------------------------------------------------------------------------------------
# THE WEB SERVER CODE
#In short, this code is like the traffic cop for incoming web requests.
#  It reads the request, figures out what the client is asking for (like setting a schedule or triggering an emergency unlock),
#  updates the shared state accordingly, and then serves the appropriate HTML page back to the client.
#It handles three main routes:
# 1. GET / → serves the main page with the current schedule and status
# 2. GET /set?start_time=&duration= → updates the schedule in the state based on the query parameters
# 3. GET /command?cmd=EMERGENCY_UNLOCK → sets the emergency unlock flag in the state
#example to make it more clear: if receive 676.767.4.1(/set?start_time=08%3A00&duration=30,
#it will parse the query string to get start_time=08:00 and duration=30 (use the funcions described above).
async def process_requests(reader, writer):
    try:
        request_line = await reader.readline()
        request_line = str(request_line, "utf-8").strip()
        print("REQ:", request_line)
 
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", b"\n"):
                break
 
        if not request_line or "favicon" in request_line: #favico is small icon that browsers request by default, we can ignore it
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return
 
        parts = request_line.split()
        if len(parts) < 2:
            await writer.wait_closed()
            return
 
        path = parts[1]                          
        if "?" in path:
            route, qs = path.split("?", 1)
        else:
            route, qs = path, ""
 
        params = parse_query(qs)
        message      = ""
        schedule_info = state["schedule_info"]
 
        #This code block is for handling the /set route, which is used to set the schedule for the study session.
        # It checks if the required parameters (start_time and duration) are present and valid, and if so,
        # it updates the shared state with the new schedule and prepares a confirmation message to show on the web page.
        if route == "/set":
            st  = params.get("start_time", "").strip()
            dur = params.get("duration",   "").strip()

            # Optional: browser sends the current wall-clock time so we can
            # set the Pico's RTC. Without this, time.localtime() returns a
            # garbage value (2000-01-01) and the start-time check never
            # matches. Add this to your index.html form:
            #   <input type="hidden" name="current_time" id="current_time">
            #   <script>
            #     const d = new Date();
            #     document.getElementById('current_time').value =
            #       String(d.getHours()).padStart(2,'0') + ':' +
            #       String(d.getMinutes()).padStart(2,'0');
            #   </script>
            current = params.get("current_time", "").strip()
            if current and ":" in current:
                try:
                    h, m = current.split(":")
                    machine.RTC().datetime(
                        (2026, 1, 1, 0, int(h), int(m), 0, 0)
                    )
                    print(f"RTC set from browser: {h}:{m}")
                except Exception as e:
                    print("RTC set error:", e)

            if st and dur and dur.isdigit() and int(dur) > 0:
                state["start_time"] = st
                state["duration"]   = int(dur)
                message = f"Schedule saved! Starting at {st} for {dur} min."
                schedule_info = message
                state["schedule_info"] = schedule_info
                state["message"] = message
                print("Schedule set:", st, dur)
                # Note: don't call lcd_show here — lcd_task is the single owner
                # of the LCD and will pick up the new schedule on its next tick
                # (within 2 s). This avoids the flicker where lcd_task overwrites
                # the schedule line back to SSID/PW two seconds after /set.
            else:
                message = "Invalid input. Please enter a valid time and duration."
 
        #This code block handles /settime?hhmm=HH:MM — the page injects a
        # tiny bit of JavaScript that calls this route automatically on load,
        # so the Pico's RTC gets set to the browser's wall-clock time without
        # any index.html edits. Without this, time.localtime() returns
        # garbage from 2000-01-01 and the start-time check never matches the
        # time displayed on the website.
        elif route == "/settime":
            hhmm = params.get("hhmm", "").strip()
            if hhmm and ":" in hhmm:
                try:
                    h, m = hhmm.split(":")
                    machine.RTC().datetime(
                        (2026, 1, 1, 0, int(h), int(m), 0, 0)
                    )
                    print(f"RTC synced from browser: {hhmm}")
                except Exception as e:
                    print("RTC set error:", e)
            # Minimal response — the browser's fetch() discards it anyway.
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return

        #This code block is for handling the /command route, which is used to trigger commands like emergency unlock from the web interface.
        # It checks the cmd parameter to see if it's an EMERGENCY_UNLOCK command, and if so, it sets the emergency_unlock flag
        #  in the state and prepares a message to show on the web page.
        elif route == "/command":
            cmd = params.get("cmd", "")
            if cmd == "EMERGENCY_UNLOCK":
                state["emergency_unlock"] = True
                message = "Emergency unlock triggered!"
                state["message"] = message
                print("EMERGENCY UNLOCK requested via web")
            elif cmd == "LOCK":
                # Manual "phone placed" confirmation from web (optional), pretty useless but why not.
                message = "Manual lock command received."
            else:
                message = f"Unknown command: {cmd}"
 
        #This code block is for handling the root route ("/"), which serves the main page of the web interface.
        # It simply prepares the current message and schedule info from the state to be displayed on the web page.
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
 
#--------------------------------------------------------------------------------------------------------------
#This code is for controlling the LCD display. It defines a function `lcd_show` 
#that takes two lines of text as input and updates the LCD display accordingly.
def lcd_show(line1="", line2=""):
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr(line1[:16])
    lcd.move_to(0, 1)
    lcd.putstr(line2[:16])
 
#This code is for mounting the SD card, which is used to store audio files for the speaker.
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
 

#This code is for playing an alarm sound through the speaker connected to the Pico.
#To control the speaker, it uses PWM to generate sound waves based on the audio data read from a WAV file stored on the SD card.
#
# IMPORTANT: play_alarm takes a `stop_fn` callback (same pattern as
# speakertest.py's play_until_stopped). The callback is checked between
# audio chunks — if it returns True, playback stops immediately. This is
# what lets the weight sensor / emergency-unlock interrupt the alarm
# instead of having to wait for the entire WAV to finish.
#
# Sound-quality tuning (from speaker2.py standalone test):
#   • 100-byte chunks instead of 256 give noticeably cleaner output.
#   • We validate both "RIFF" and "WAVE" in the header to reject
#     half-corrupt / wrong-format files early.
#   • stop_fn is only called once every CHECK_EVERY_N_CHUNKS chunks
#     rather than every chunk. Calling it every chunk caused audible
#     glitches because hx711.get_units() inside the callback can block
#     for up to ~100 ms waiting for the HX711 data-ready signal.
#     Checking less often keeps the audio smooth and still responds to
#     the phone being placed well within the 2-second spec.
#
# Returns True if stopped early, False if the file played to the end.
def play_alarm(stop_fn):
    pin = machine.PWM(machine.Pin(22))
    pin.freq(20000)
    stopped_early = False
    CHECK_EVERY_N_CHUNKS = 40   # at sr=8 kHz this is a stop-check every ~500 ms
    try:
        with open("/sd/motivation2.wav", "rb") as f:
            hdr = f.read(44)
            # Stricter header check (from speaker2.py): require both
            # "RIFF" and "WAVE" markers before trusting the file.
            if len(hdr) < 44 or hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
                print("Not a valid WAV file")
                return False
            sr = int.from_bytes(hdr[24:28], "little")
            delay = int(500_000 / sr)
            print("sample_rate", sr, "delay_us", delay)
            chunk_count = 0
            while True:
                # Only run the potentially-slow stop callback occasionally,
                # so audio stays smooth. The first iteration still checks
                # (chunk_count==0) so a pre-existing stop condition exits
                # immediately.
                if chunk_count % CHECK_EVERY_N_CHUNKS == 0 and stop_fn():
                    stopped_early = True
                    break
                chunk = f.read(100)  # 100-byte chunks give better audio than 256
                if not chunk:
                    break
                for byte in chunk:
                    pin.duty_u16(byte * 257) # Scale 0-255 to 0-65535 for PWM duty cycle
                    utime.sleep_us(delay)
                chunk_count += 1
    except Exception as e:
        print("Speaker error:", e)
    finally:
        pin.duty_u16(0) # Stop sound
        pin.deinit()
    return stopped_early
 
#------------------------------------------------------------------------------------------------------------------------
#This code is for controlling the box mechanism using the stepper motor and linear actuator.
def close_box():
    print("Closing box...")
    stepper.step(-STEPS_TO_CLOSE) # Negative direction to close — adjust sign if your wiring is opposite
   

#This code is for opening the box mechanism using the stepper motor and linear actuator.
def open_box():
    print("Opening box...")
    stepper.step(STEPS_TO_CLOSE) # Positive direction to open — adjust sign if your wiring is opposite

#------------------------------------------------------------------------------------------------------------------------
# This code is for getting the current local time in "HH:MM" format, which is used for scheduling the study sessions.
def current_hhmm():
    t = time.localtime() # get local time by calling time.localtime(), which returns a tuple with the current time and date information, and then formats it into a string showing just the hours and minutes.
    #time.localtime() gets the real time from the Pico's internal clock
    return f"{t[3]:02d}:{t[4]:02d}"
 
def current_hhmmss():
    t = time.localtime()
    return f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

#------------------------------------------------------------------------------------------------------------------------
# This code defines the async task for updating the LCD display. It runs in an infinite loop,
#  checking the current state of the session and updating the display accordingly.
#Shows IP adress and Wi-Fi credentials when idle, shows current time and session status during an active session.
async def lcd_task():
    display_ip = False
    while True:
        if state["session_active"]:
            # During a session the weight_and_session_task handles the LCD
            # directly (countdown, emergency messages, etc.), so lcd_task
            # steps aside and does nothing here.
            pass
        elif state["start_time"] and state["duration"]:
            # A schedule has been received from the website but the session
            # hasn't started yet → show the schedule. This is what makes the
            # LCD react to the dictionary being updated by /set.
            lcd_show(f"Start: {state['start_time']}",
                     f"Dur: {state['duration']} min")
        else:
            # Idle: alternate between SSID/PW and SSID/IP every 2 seconds
            if display_ip:
                lcd_show(f"SSID:{SSID}", f"IP: {state['ip']}")
            else:
                lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")
            display_ip = not display_ip
        await asyncio.sleep(2)
 
#---------------------------------------------------------------------------------------------------------
#WEIGHT SENSOR AND SESSION LOGIC TASK
#This code defines the main logic for monitoring the weight sensor and 
# managing the study session state. (kind of the "brain" of the BlackLock)
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
    loop_counter = 0

    while True:
        # Debug every ~10 iterations (10 s) so the user can see what the
        # Pico's clock reads vs. the scheduled time. If RTC=00:00 (or some
        # year-2000 garbage) while the website shows 14:32, the browser's
        # /settime sync isn't reaching the Pico — check the console for
        # "RTC synced from browser" lines. If the clock looks right but
        # start_time comparison still fails, it's a string mismatch
        # (e.g., leading zero).
        loop_counter += 1
        if loop_counter % 10 == 0 and state["start_time"]:
            print(f"[idle] Pico clock={current_hhmm()}  waiting for={state['start_time']}  duration={state['duration']}")

        # ── 1. Decide whether to start a session ────────────────────────
        # A session can start two ways — either is enough as long as
        # `duration` has been set via the website:
        #   (a) scheduled time hit:  current_hhmm() == state["start_time"]
        #   (b) phone placed:        weight >= THRESHOLD
        # (b) is what makes "should always be able to close" work — the
        # box reacts to the phone even if start_time hasn't been reached,
        # or even if start_time was never set at all.
        if not state["session_active"]:
            # Read the weight once per outer loop — cheap, ~0.1 s.
            try:
                w_now = hx711.get_units()
            except Exception:
                w_now = 0.0
            phone_already_there = w_now >= THRESHOLD

            has_schedule = state["start_time"] and state["duration"]
            time_trigger = has_schedule and current_hhmm() == state["start_time"]
            # Weight trigger no longer requires duration to be set — if the
            # phone is dropped on the sensor with no duration configured,
            # we fall back to DEFAULT_DURATION below.
            weight_trigger = phone_already_there

            if time_trigger or weight_trigger:
                # Apply the default duration if none was set via the website.
                # time_trigger can't get here without duration already set
                # (has_schedule requires it), so this only fires for pure
                # weight-triggered sessions.
                if not state["duration"]:
                    state["duration"] = DEFAULT_DURATION
                    print(f"No duration set — defaulting to {DEFAULT_DURATION} min")

                print(f"Starting session  (time={time_trigger}, weight={weight_trigger})")
                state["session_active"]   = True
                state["phone_in_box"]     = phone_already_there
                state["emergency_unlock"] = False

                # ── 2. Alarm phase — ONLY if phone not already placed ─
                if not state["phone_in_box"]:
                    print("Playing alarm – put your phone in the box!")
                    lcd_show("PUT PHONE IN BOX", "")

                    # Stop callback for play_alarm — checked between audio
                    # chunks so the speaker stops within ~30 ms of the
                    # phone being detected or the emergency button being
                    # pressed.
                    def phone_or_unlock():
                        if state["emergency_unlock"]:
                            return True
                        try:
                            w = hx711.get_units()
                            if w >= THRESHOLD:
                                state["phone_in_box"] = True
                                print(f"Phone detected at {w:.1f} g")
                                return True
                        except Exception as e:
                            print("HX711 read error:", e)
                        return False

                    while not state["phone_in_box"] and not state["emergency_unlock"]:
                        if sd_ok:
                            play_alarm(phone_or_unlock)
                        else:
                            phone_or_unlock()
                        await asyncio.sleep(0.1)

                    # Emergency during alarm → abort without closing
                    if state["emergency_unlock"]:
                        print("Emergency unlock during alarm — aborting session")
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
                    print("Phone already on sensor — skipping alarm, closing now")

                # ── 3. Close & lock box ───────────────────────────────
                close_box()
                lcd_show("Session locked", f"{state['duration']} min")

                # ── 4. Monitor + grace + possible re-lock ─────────────
                # The outer `while True` lets us re-enter the countdown
                # phase if the user leaves their phone in the box past
                # the grace period — in that case we close again for
                # another DEFAULT_DURATION session.
                total_sec = state["duration"] * 60
                phone_removed = False

                while True:
                    # ── Countdown ─────────────────────────────────
                    # Counter-based instead of timestamp-based so every
                    # value from total_sec down to 00:00 actually gets
                    # displayed. The previous version used
                    # `while time.time() < session_end` which exits the
                    # moment remaining hits 0, so the user never saw
                    # "Left: 00:00" — the box appeared to open "early".
                    #
                    # Tick-anchored sleep: each iteration does LCD I/O
                    # (~50-100 ms on I²C at 10 kHz), so a plain
                    # `asyncio.sleep(1)` makes every frame take ~1.05 s.
                    # Over time the `Left:` counter ticks slower than
                    # the wall-clock display on line 1, and the wall
                    # clock visibly skips seconds. Anchoring to
                    # time.ticks_ms() from the start of the countdown
                    # keeps both lines in sync no matter how long the
                    # LCD write takes.
                    aborted = False
                    countdown_start = time.ticks_ms()
                    for frame in range(total_sec):
                        remaining_sec = total_sec - frame
                        if state["emergency_unlock"]:
                            aborted = True
                            break
                        # Only redraw the LCD every LCD_REFRESH_SEC frames
                        # (the first frame always redraws). The timing loop
                        # still runs once per second so the wall clock and
                        # emergency check stay responsive — only the actual
                        # lcd.clear()+putstr is throttled to cut flicker.
                        if frame % LCD_REFRESH_SEC == 0:
                            mins, secs = divmod(remaining_sec, 60)
                            lcd_show(current_hhmmss(),
                                     f"Left: {mins:02d}:{secs:02d}")
                        target = time.ticks_add(countdown_start,
                                                (frame + 1) * 1000)
                        delay_ms = time.ticks_diff(target, time.ticks_ms())
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000)

                    # Show 00:00 for a beat so the user actually sees
                    # the countdown finish before anything else happens.
                    if not aborted:
                        lcd_show(current_hhmmss(), "Left: 00:00")
                        await asyncio.sleep(1)

                    # ── Open box ──────────────────────────────────
                    if aborted:
                        print("Emergency unlock!")
                        lcd_show("EMERGENCY UNLOCK", "Opening...")
                    else:
                        # User-requested message so it's obvious what's
                        # happening while the stepper is running.
                        lcd_show("Box is opening", "Take your phone")
                    open_box()

                    # Clear the emergency flag now that we've acted on it.
                    # Previously emergency unlock skipped the grace period
                    # entirely and dumped straight to idle, which gave the
                    # user no time to actually remove the phone. Now an
                    # emergency press just brings forward the normal
                    # end-of-session opening, and the grace period still
                    # runs so the user has GRACE_SECONDS to lift the phone
                    # before auto re-lock.
                    state["emergency_unlock"] = False

                    # ── Grace period ──────────────────────────────
                    # Give the user GRACE_SECONDS (default 2 min) to
                    # lift their phone off the sensor. If they don't,
                    # the box re-locks for another DEFAULT_DURATION
                    # session (anti-procrastination).
                    #
                    # Debounce: require GRACE_DEBOUNCE consecutive
                    # below-threshold readings before concluding the
                    # phone has been removed. Without this, vibration
                    # from the stepper that just ran open_box() makes
                    # the very first HX711 read dip below threshold
                    # and the grace period ends immediately.
                    await asyncio.sleep(GRACE_SETTLE_SEC)
                    phone_removed = False
                    below_count = 0
                    grace_start = time.ticks_ms()
                    for sec_idx in range(GRACE_SECONDS):
                        remaining_sec = GRACE_SECONDS - sec_idx
                        # The box is already open during grace, so an
                        # emergency press has nothing to unlock. Just
                        # absorb the flag so it doesn't hang around and
                        # trigger anything on the next session.
                        if state["emergency_unlock"]:
                            state["emergency_unlock"] = False
                            print("Emergency press during grace — box already open, ignoring")
                        try:
                            w = hx711.get_units()
                        except Exception as e:
                            print("HX711 read error:", e)
                            # Treat read errors as "phone still there"
                            # so a flaky sensor can't end the session.
                            w = THRESHOLD

                        if w < THRESHOLD:
                            below_count += 1
                            if below_count >= GRACE_DEBOUNCE:
                                phone_removed = True
                                print(f"Phone removed ({w:.1f} g) — session ends")
                                break
                        else:
                            below_count = 0   # reset on any above-threshold sample

                        # Throttle LCD redraws to cut flicker while still
                        # polling the load cell every second.
                        if sec_idx % LCD_REFRESH_SEC == 0:
                            mins, secs = divmod(remaining_sec, 60)
                            lcd_show("Take phone out!",
                                     f"Close: {mins:02d}:{secs:02d}")
                        target = time.ticks_add(grace_start,
                                                (sec_idx + 1) * 1000)
                        delay_ms = time.ticks_diff(target, time.ticks_ms())
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000)

                    if phone_removed:
                        break   # exit the outer re-lock loop

                    # Phone still there → re-lock for another round
                    print(f"Phone not removed — re-locking for {DEFAULT_DURATION} min")
                    lcd_show("Phone still in", "Re-locking...")
                    await asyncio.sleep(2)   # let user read the message
                    close_box()
                    total_sec = DEFAULT_DURATION * 60
                    state["duration"] = DEFAULT_DURATION
                    # loop back to the countdown with fresh total_sec

                # ── 5. Reset state back to idle ───────────────────────
                state["session_active"]   = False
                state["phone_in_box"]     = False
                state["emergency_unlock"] = False
                state["start_time"]       = None
                state["duration"]         = None
                state["message"]          = "Session complete! Great work."
                state["schedule_info"]    = ""
                print("Session complete.")

        # Poll once a second so a phone placed on the sensor triggers
        # a session within ~1 s (was 5 s — too slow to feel responsive).
        await asyncio.sleep(1)
 
# the main function to start the access point, launch the web server,
# and run all tasks concurrently
async def main():
    state["ip"] = create_ap(SSID, PASSWORD)
    if not state["ip"]:
        print("Cannot start without access point. waiting......")
        return
 
    lcd_show(f"SSID:{SSID}", f"PW:{PASSWORD}")
 
    # Start async web server (callback-based — no manual accept() loop needed)
    server = await asyncio.start_server(process_requests, "0.0.0.0", 80)
    print(f"Web server listening on http://{state['ip']}/")
 
    # Run all tasks concurrently
    await asyncio.gather(
        lcd_task(),
        weight_and_session_task(),
    )
    # `server` runs in the background automatically once awaited above
 
 # It ensures our code starts correctly and handles unexpected, exits gracefully.
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
    except Exception as e:
        print("Fatal error:", e)