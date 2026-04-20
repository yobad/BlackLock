"""
Description: This is the main file running the BlackLock. It includes:
wifi hotspot created by Pico, async weight sensor monitoring, async web server,
LCD display updates, and speaker playback.
Date: Apr 14, 2026
Author: The BlackLock Team 
This file was generated using Claude.ai, we put our own code (html website, speaker, internet and motor files) as 
reference so that the AI could put them all together
Here's a link to the discussion: https://claude.ai/share/9485b631-bc30-4606-8e16-da5940993260
 

  - Shared `state` dict is the single source of truth for all tasks
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
DATA_PIN  = Pin(5, Pin.IN, pull=Pin.PULL_DOWN)
CLOCK_PIN = Pin(6, Pin.OUT) # sends clock signal to HX711 to trigger readings
SCALE_F   = 1138.6597 #value to convert raw HX711 readings to grams — tweak after calibration
THRESHOLD = 170          # grams — adjust based on your phone's weight + box setup
 
# Stepper motor
STEP_IN1, STEP_IN2, STEP_IN3, STEP_IN4 = 14, 12, 13, 15 # stepper motor pins (adjust to match wiring)
STEPS_TO_CLOSE = 1028 # number of steps to fully close the box — adjust based on stepper motor and gearing
 
# Linear actuator pin setup (adjust based on our wiring)
LIN_IN1 = Pin(21, Pin.OUT)
LIN_IN2 = Pin(10, Pin.OUT)
LIN_ENA = PWM(Pin(11)) # PWM pin for speed control of the linear actuator
 
# LCD pin setup (adjust if using different pins)
#0x27 is the common I2C address for our LCD, if different we can change it here
I2C_ADDR   = 0x27
I2C_NUM_ROWS, I2C_NUM_COLS = 2, 16
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400_000)
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
#LINEAR ACTUATOR SETUP
lin_act = DCMotor(LIN_IN1, LIN_IN2, LIN_ENA)
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
def play_alarm():
    pin = machine.PWM(machine.Pin(22))
    pin.freq(20_000)
    try:
        with open("/sd/motivation2.wav", "rb") as f:
            hdr = f.read(44)
            if len(hdr) < 44: # Basic check to ensure it's a valid WAV file with a proper header
                return
            sr = int.from_bytes(hdr[24:28], "little")
            delay = int(500_000 / sr) # delay in microseconds between samples, based on sample rate
            while True:
                chunk = f.read(256)
                if not chunk:
                    break
                for byte in chunk:
                    pin.duty_u16(byte * 257) # Scale 0-255 to 0-65535 for PWM duty cycle
                    utime.sleep_us(delay)
    except Exception as e:
        print("Speaker error:", e)
    finally:
        pin.duty_u16(0) # Stop sound
        pin.deinit()
 
#------------------------------------------------------------------------------------------------------------------------
#This code is for controlling the box mechanism using the stepper motor and linear actuator.
def close_box():
    print("Closing box...")
    stepper.step(STEPS_TO_CLOSE) # Step the motor to close the box, adjust STEPS_TO_CLOSE in config based on your specific motor and gearing
    lin_act.forward(100) # Extend the linear actuator to lock the box, modify value if it's too fast or slow
    time.sleep(2)
    lin_act.stop()

#This code is for opening the box mechanism using the stepper motor and linear actuator.
def open_box():
    print("Opening box...")
    lin_act.backwards(100) # Retract the linear actuator to unlock the box, modify value if it's too fast or slow
    time.sleep(2)
    lin_act.stop()
    stepper.step(-STEPS_TO_CLOSE) # Step the motor in reverse to open the box, adjust STEPS_TO_CLOSE in config based on your specific motor and gearing

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
            # Show current time + session running indicator
            lcd_show(current_hhmmss(), "Session active")
        else:
            # Alternate between showing PW and IP every 2 seconds
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
















