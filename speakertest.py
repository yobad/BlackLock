"""
Description: This is a test file created to test the speaker system of the BlackLock.
Objectives defined below.
Date: Apr 23, 2026
Author: The BlackLock Team 
This file was generated using Claude.ai, we put our main code file (main.py) and the word document that we 
created to design the tests as reference so that the AI could put them all together
Here's a link to the discussion: https://claude.ai/share/153a9703-121d-4fb9-8fca-2f2ae00f5fe7

Objective:
  - Verify the speaker plays sound at the study session start time
  - Verify the sound stops when a phone is placed on the load cell (within 2s)

No website, no LCD, no motors needed.

Test cases:
  Case 1 — Start time set   → silence until start time, then sound plays
  Case 2 — Phone detected   → sound stops within 2s of weight threshold

How to run:
  1. Press on the run button at the bottom of VS Code
  3. Wait silently for the start time (Case 1)
  4. Once sound starts, place phone on load cell (Case 2)
"""

import time
import machine
import utime
import os
import sdcard
import vfs
from hx711_gpio import HX711
from machine import Pin

# ── Config ────────────────────────────────────────────────────────────────────
SCALE_F   = 1138.6597
THRESHOLD = 170          # grams — tweak after calibration
WAV_FILE  = "/sd/motivation2.wav"

# ── Hardware init: load cell ──────────────────────────────────────────────────
print("Taring load cell — remove all weight now...")
data_pin  = Pin(6, Pin.IN, pull=Pin.PULL_DOWN)
clock_pin = Pin(5, Pin.OUT)
hx711 = HX711(clock_pin, data_pin)
hx711.tare()
hx711.set_scale(SCALE_F)
print("Tare complete.\n")

# ── SD card mount ─────────────────────────────────────────────────────────────
def mount_sd():
    try:
        cs  = machine.Pin(17, machine.Pin.OUT)
        spi = machine.SPI(0, baudrate=1_000_000,
                          sck=machine.Pin(18), mosi=machine.Pin(19), miso=machine.Pin(16))
        sd = sdcard.SDCard(spi, cs)
        vfs.mount(vfs.VfsFat(sd), "/sd") #type: ignore
        print("SD mounted. Files:", os.listdir("/sd"))
        return True
    except Exception as e:
        print("SD mount FAILED:", e)
        return False

# ── Speaker: plays chunk-by-chunk, calls stop_fn between each chunk ───────────
def play_until_stopped(stop_fn):
    """
    Plays WAV file. Calls stop_fn() between every 256-byte chunk.
    Returns True if stopped early by stop_fn, False if file ended.
    The weight check between chunks means stop latency < 1s at 8kHz.
    """
    pin = machine.PWM(machine.Pin(22))
    pin.freq(20_000)
    stopped_early = False
    try:
        with open(WAV_FILE, "rb") as f:
            hdr = f.read(44)
            if len(hdr) < 44 or hdr[0:4] != b"RIFF":
                print("Not a valid WAV file")
                return False
            sr    = int.from_bytes(hdr[24:28], "little")
            delay = int(500_000 / sr)
            print(f"[SPEAKER] Playing at {sr}Hz — place phone to stop")
            while True:
                if stop_fn():
                    stopped_early = True
                    break
                chunk = f.read(256)
                if not chunk:
                    print("[SPEAKER] File ended — re-run and place phone sooner")
                    break
                for byte in chunk:
                    pin.duty_u16(byte * 257)
                    utime.sleep_us(delay)
    except Exception as e:
        print("Speaker error:", e)
    finally:
        pin.duty_u16(0)
        pin.deinit()
    return stopped_early

# ── Weight check function ─────────────────────────────────────────────────────
_detect_time = None

def phone_detected():
    global _detect_time
    try:
        w = hx711.get_units()
        print(f"[WEIGHT] {w:.1f}g", end="  ")
        if w >= THRESHOLD:
            print("<-- PHONE DETECTED")
            _detect_time = time.time()
            return True
        print()
        return False
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# CASE 1: Silence before start time
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 50)
print("CASE 1 & 2: Speaker alarm test")
print("=" * 50)
print("Enter how many minutes from NOW the session should start.")
print("Recommended: 1 minute (verifies silence, then sound plays)")
offset_str = input("Minutes until start: ").strip()
try:
    offset_minutes = int(offset_str)
except ValueError:
    offset_minutes = 1
    print("Invalid — defaulting to 1 minute")

start_epoch = time.time() + offset_minutes * 60
print(f"\n[CASE 1] Session starts in {offset_minutes} min — NO sound should play now")

while time.time() < start_epoch:
    remaining = int(start_epoch - time.time())
    print(f"  Waiting {remaining}s ... (silence expected)")
    time.sleep(5)

print("\n[CASE 1] Start time reached!")
result1 = input("Was there silence during the wait? (y/n): ").strip().lower()
print(f"Case 1 result: {'PASS' if result1 == 'y' else 'FAIL'}\n")

# ─────────────────────────────────────────────────────────────────────────────
# CASE 2: Sound stops when phone placed (within 2s)
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 50)
print("CASE 2: Sound should start NOW — place phone to stop it (within 2s)")
print("=" * 50)

if not mount_sd():
    print("Cannot mount SD — speaker test aborted")
else:
    _detect_time  = None
    alarm_started = time.time()
    stopped       = play_until_stopped(phone_detected)
    stop_time     = time.time()

    if stopped and _detect_time is not None:
        latency = stop_time - _detect_time
        print(f"\n[CASE 2] Sound stopped {latency:.2f}s after phone detected")
        if latency <= 2.0:
            print("Case 2 result: PASS (within 2s requirement)")
        else:
            print(f"Case 2 result: FAIL ({latency:.2f}s exceeds 2s requirement)")
    else:
        print("\n[CASE 2] WAV file ended before phone was detected.")
        print("  Re-run and place the phone sooner after sound starts.")

print("\nTest 5 complete.")
