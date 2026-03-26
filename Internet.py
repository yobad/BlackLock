import network
import time
import socket
import os
state = "off"

ssid = "ssid" #change to the name of the network
password = "password" #change to the password of the network

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

    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass  # not supported on some MicroPython ports

    try:
        s.bind(adress)
    except OSError as e:
        s.close()
        if hasattr(e, 'args') and e.args and e.args[0] == 98:
            print("Address already in use, retrying...")
            time.sleep(1)
            return open_socket()
        raise

    s.listen(3)
    return s


def web_page():
    html = None
    
    try:
        with open('index.html', "r") as f:
            html = f.read()
        print(html) 
    except OSError:
        pass

    if html is None:
        html = """
        <!DOCTYPE html>
        <html><head><title>Error</title></head><body>
        <h1>Error loading index.html</h1>
        <p>File was not found in expected locations.</p>
        </body></html>
        """
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



