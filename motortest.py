"""
Description: This is a test file created to test the motor system of the BlackLock.
Objectives defined below.
Date: Apr 23, 2026
Author: The BlackLock Team 
This file was generated using Claude.ai, we put our main code file (main.py) and the word document that we 
created to design the tests as reference so that the AI could put them all together
Here's a link to the discussion: https://claude.ai/share/153a9703-121d-4fb9-8fca-2f2ae00f5fe7
===========================================================
This file was repurposed to open the lid if the phone gets stuck in the box. 
Running the file should open the lid (threshold set to -100).
60s to remove the phone and stop the test in VS Code before the lid opens again (threshold set to -100).
"""

import time
from hx711_gpio import HX711
from machine import Pin, PWM
import motor

# ── Config ────────────────────────────────────────────────────────────────────
SCALE_F         = 1138.6597
THRESHOLD       = -100 # grams — Assure that lid opens
STEPS_TO_CLOSE  = 1700
SESSION_SECONDS = 30   # Be sure to have time to remove the phone and press stop in VS Code

# ── Hardware init ─────────────────────────────────────────────────────────────
print("Taring load cell — remove all weight now...")
data_pin  = Pin(6, Pin.IN, pull=Pin.PULL_DOWN)
clock_pin = Pin(5, Pin.OUT)
hx711 = HX711(clock_pin, data_pin)
hx711.tare()
hx711.set_scale(SCALE_F)
print("Tare complete.\n")

stepper = motor.FullStepMotor.frompins(14, 12, 13, 15)

LIN_IN1 = Pin(21, Pin.OUT)
LIN_IN2 = Pin(10, Pin.OUT)
LIN_ENA = PWM(Pin(11))


# ── Motor helpers ─────────────────────────────────────────────────────────────
def close_box():
    print("[MOTOR] Stepper closing lid...")
    stepper.step(STEPS_TO_CLOSE)
    print("[MOTOR] Box LOCKED\n")

def open_box():
    print("[MOTOR] Stepper opening lid...")
    stepper.step(STEPS_TO_CLOSE)
    print("[MOTOR] Box OPEN\n")

# ─────────────────────────────────────────────────────────────────────────────
# CASE 1: No phone — lid should stay open
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 50)
print("CASE 1: No phone placed — lid should stay OPEN")
print(f"Taking 5 readings. Threshold = {THRESHOLD}g")
print("=" * 50)

all_below = True
for i in range(5):
    weight = hx711.get_units()
    status = "PASS — below threshold" if weight < THRESHOLD else "FAIL — unexpected weight detected"
    print(f"  Reading {i+1}: {weight:.1f}g  →  {status}")
    if weight >= THRESHOLD:
        all_below = False
    time.sleep(1)

print(f"\nCase 1 result: {'PASS' if all_below else 'FAIL'}\n")

# ─────────────────────────────────────────────────────────────────────────────
# CASE 2: Phone placed — close, lock, wait, unlock, open
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 50)
print("CASE 2: Place phone/weight on the load cell now")
print(f"Waiting for weight >= {THRESHOLD}g ...")
print("=" * 50)

while True:
    weight = hx711.get_units()
    print(f"  Weight: {weight:.1f}g", end="\r")
    if weight >= THRESHOLD:
        print(f"\n  Phone detected at {weight:.1f}g — closing box in 2s")
        break
    time.sleep(0.3)

time.sleep(2)
open_box()

print(f"[SESSION] Locked for {SESSION_SECONDS}s")
print("[TEST]    Try forcing the lid open now — it should resist\n")
time.sleep(SESSION_SECONDS)

print("[SESSION] Time elapsed — opening box")
close_box()
print("Case 2 result: PASS if lid closed, locked, then unlocked and opened\n")

# ─────────────────────────────────────────────────────────────────────────────
# CASE 3: Phone stays in box after opening — lid should NOT re-close
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 50)
print("CASE 3: Leave phone in box — lid should stay OPEN")
print("Monitoring for 10s — no re-close should occur")
print("=" * 50)

for i in range(10):
    weight = hx711.get_units()
    print(f"  {i+1}s: {weight:.1f}g — lid open (no re-close expected)")
    time.sleep(1)

print("\nCase 3 result: PASS if the lid did not close during those 10 seconds")
print("\nTest 2 complete.")
