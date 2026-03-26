import network
import time
import socket

state = "off"

ssid = "ssid" #change to the name of the network
password = "password"

def connect_to_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    while not wlan.isconnected():
        print("Connecting to Wi-Fi...")
        time.sleep(1)
    print("Connected!")

def open_socket():
    adress = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.bind(adress)
    s.listen(3)
    return s

def web_page():
    html = """<!DOCTYPE html>
    <html>
    <head>
        <title>ESP32 Web Server</title>
    </head>
    <body>
        <h1>Hello, World!</h1>
        <p>This is a simple web page served by the ESP32.</p>
        <p> I can write anything I want here, and it will be displayed on the webpage. </p>
        <p>State: {}</p>
    </body>
    </html>""".format(state)
    return html
connect_to_wifi()

s = open_socket()
wlan = network.WLAN(network.STA_IF)
print("Server is running on http://{}:80".format(wlan.ifconfig()[0]))

try: 
    while True:
        client, addr = s.accept()
        request = client.recv(1024)
        request = str(request, 'utf-8')
        print("Request:", wlan.ifconfig()[0])

        html = web_page()
        client.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n")
        client.send(html)
        client.close()
except OSError as e:
    print("An error occurred: {}".format(e))



