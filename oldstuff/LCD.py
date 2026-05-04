'''
This code connects the Pico W to the internet in order to fetch the current UTC time, then converts
the time to the EST and finally displays it onto and LCD screen.

This code uses the I2C LCD libraries created by Tyler Peppy et al., which can be found here:
https://github.com/T-622/RPI-PICO-I2C-LCD/tree/main
'''

from machine import Pin, I2C
from pico_i2c_lcd import I2cLcd
from time import localtime, sleep
import ntptime
import network

I2C_NUM_ROWS = 2
I2C_NUM_COLS = 16

i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=10000)

lcd = I2cLcd(i2c, 0x27, I2C_NUM_ROWS, I2C_NUM_COLS)

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect("SSID", "Password") #change SSID and password to the name of the network and its password
while not wlan.isconnected():
    print("Connecting to Wi-Fi...")
    sleep(1)

ntptime.settime()

offset = -4 #change for when no longer daylight savings time

while True:
    t = localtime()
    hour = (t[3] + offset) % 24
    time_str = "{:02d}:{:02d}:{:02d}".format(hour, t[4], t[5])
    
    lcd.move_to(0,0)
    lcd.putstr("Current time:")
    lcd.move_to(0,1)
    lcd.putstr(f"{time_str}")