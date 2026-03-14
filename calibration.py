"""
Calibration script for the load cell. Sets the units to grams and computes a scale factor, by which
the raw reading of the load cell is divided by.

This code contains the HX711 amplifier library created by Robert Hammelrath, which can be found
in the following link: https://github.com/robert-hh/hx711 

"""




from machine import Pin
from hx711_gpio import HX711
import time

data_pin = Pin(5, Pin.IN, pull=Pin.PULL_DOWN)
clock_pin = Pin(6, Pin.OUT)
hx711 = HX711(clock_pin, data_pin)

print("taring...")
time.sleep(3)
hx711.tare()
print(f"Offset: {hx711.OFFSET}")


known_weight = 9  # grams

print("Place", known_weight, "g on the scale.")
time.sleep(5)

raw = hx711.read_average(20)
value = raw - hx711.OFFSET

print("Raw value:", raw)
print("Value minus offset:", value)

scale = value / known_weight
hx711.set_scale(scale)

print("Scale factor:", scale)

while True:
    print("Weight:", hx711.get_units(), "g")
    time.sleep(0.5)
