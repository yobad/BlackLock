"""
Description: This is a test file created to test the LCD system of the BlackLock.
Objectives defined below.
Date: Apr 21, 2026
Author: The BlackLock Team 
This file was generated using Claude.ai, we put our main code file (main.py) and the word document that we 
created to design the tests as reference so that the AI could put them all together
Here's a link to the discussion: https://claude.ai/share/153a9703-121d-4fb9-8fca-2f2ae00f5fe7


======================================
Objective:
  - Verify the LCD shows the correct session info when set
  - Verify the LCD countdown updates in real time and matches the console

No website, no motors, no load cell, no SD card needed.

Test cases:
  Case 1 — Session info set  → appears correctly on LCD
  Case 2 — Countdown running → LCD updates every second and matches console

How to run:
  1. Press on the play button at the bottom of VS Code
  2. Follow the prompts in the console to verify the LCD output
"""

import time
from machine import I2C, Pin
from pico_i2c_lcd import I2cLcd

# ── LCD config — update to match your wiring ──────────────────────────────────
I2C_ADDR  = 0x27 
I2C_BUS   = 0
SDA_PIN   = 0
SCL_PIN   = 1
NUM_ROWS  = 2
NUM_COLS  = 16

# ── Test config ───────────────────────────────────────────────────────────────
SESSION_MINUTES = 1   # Short so the countdown test doesn't take long

# ── LCD init ──────────────────────────────────────────────────────────────────
print("Scanning I2C bus...")
i2c = I2C(I2C_BUS, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=10000)
found = i2c.scan()
print("Devices found:", [hex(d) for d in found])

if I2C_ADDR not in found:
    print(f"WARNING: 0x{I2C_ADDR:02X} not found on bus.")
    print("Update I2C_ADDR at the top of this file and re-run.")

print("Initialising LCD...")
lcd = I2cLcd(i2c, I2C_ADDR, NUM_ROWS, NUM_COLS)
print("LCD ready.\n")

def lcd_show(line1="", line2=""):
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr(line1[:NUM_COLS])
    lcd.move_to(0, 1)
    lcd.putstr(line2[:NUM_COLS])

# ─────────────────────────────────────────────────────────────────────────────
# CASE 1: Session info appears on LCD
# ─────────────────────────────────────────────────────────────────────────────
SESSION_MINUTES = int(input("Enter session length in minutes (for testing, use we recommend using 1): ").strip())

print("=" * 50)
print(f"CASE 1: Display session info on LCD")
print(f"  Expected LCD line 1: 'Session:{SESSION_MINUTES}min'")
print(f"  Expected LCD line 2: 'Starting soon...'")
print("=" * 50)

line1 = f"Session:{SESSION_MINUTES}min"
line2 = "Starting soon..."
lcd_show(line1, line2)
print(f"[CONSOLE] Sent to LCD: '{line1}' / '{line2}'")
print("[CHECK]   Confirm the LCD shows the text above (5s to look)...")
time.sleep(5)

result1 = input("Did the LCD show the correct session info? (y/n): ").strip().lower()
print(f"Case 1 result: {'PASS' if result1 == 'y' else 'FAIL'}\n")

# ─────────────────────────────────────────────────────────────────────────────
# CASE 2: Countdown on LCD matches console
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 50)
print("CASE 2: Countdown — compare LCD to console each second")
print(f"Counting down {SESSION_MINUTES} minute(s)...")
print("=" * 50)

total_seconds = SESSION_MINUTES * 60
start_t = time.time()
mismatches = 0

while True:
    remaining = total_seconds - int(time.time() - start_t)

    if remaining <= 0:
        lcd_show("Session done!", "")
        print("[CONSOLE] Session done!")
        break

    mins     = remaining // 60
    secs     = remaining % 60
    time_str = f"{mins:02d}:{secs:02d}"

    lcd_show("Time left:", time_str)
    print(f"[CONSOLE] Remaining: {time_str}  ← LCD should show this")
    time.sleep(1)

print()
result2 = input("Did the LCD countdown match the console every second? (y/n): ").strip().lower()
print(f"Case 2 result: {'PASS' if result2 == 'y' else 'FAIL'}")
print("\nThe LCD test is complete!")
