from hx711_gpio import HX711
from machine import Pin

data_pin = Pin(5, Pin.IN, pull=Pin.PULL_DOWN)
clock_pin = Pin(6,Pin.OUT)

hx711 = HX711(clock_pin, data_pin)

hx711.tare()

while(1):
    raw_wt = hx711.read()
    sf = 340/350000

    weight = raw_wt*sf
    print(f"{weight:.1f} grams")