'''
This code connects the Pico W to the internet in order to fetch the current UTC time, then converts
the time to the EST and finally displays it onto an LCD screen.

This code uses the I2C LCD libraries created by Tyler Peppy et al., which can be found here:
https://github.com/T-622/RPI-PICO-I2C-LCD/tree/main
'''

import network
import socket
import ntptime
from machine import Pin, I2C
from time import localtime, sleep
from pico_i2c_lcd import I2cLcd

I2C_NUM_ROWS = 2
I2C_NUM_COLS = 16

ssid = "ssdi"
password = "password"

i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=10000)
lcd = I2cLcd(i2c, 0x27, I2C_NUM_ROWS, I2C_NUM_COLS)

def connect_to_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    while not wlan.isconnected():
        print("Connecting to Wi-Fi...")
        sleep(1)
    print("Connected to Wi-Fi")
    return wlan


def open_socket():
    address = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    s.bind(address)
    s.listen(3)
    s.settimeout(0.25)
    return s


def web_page():
    try:
        with open('index.html', 'r') as f:
            return f.read()
    except OSError:
        return """
        <!DOCTYPE html>
        <html><head><title>Error</title></head><body>
        <h1>Error loading index.html</h1>
        <p>File was not found.</p>
        </body></html>
        """


def update_lcd(offset=-4):
    t = localtime()
    hour = (t[3] + offset) % 24
    time_str = "{:02d}:{:02d}:{:02d}".format(hour, t[4], t[5])
    lcd.move_to(0, 0)
    lcd.putstr("Current time:")
    lcd.move_to(0, 1)
    lcd.putstr(time_str + "    ")


wlan = connect_to_wifi()
ntptime.settime()
server = open_socket()
print("Server is running on http://{}:80".format(wlan.ifconfig()[0]))

while True:
    update_lcd(offset=-4)

    try:
        client, addr = server.accept()
        request = client.recv(1024)
        print("HTTP request from", addr)
        html = web_page()
        response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html
        client.send(response.encode())
        client.close()
    except OSError:
        pass

    sleep(1)




