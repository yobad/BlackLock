
"""
BlackLock - Connection Test (No Hardware Modules)
Tests WiFi hotspot + web server only.
Upload this as main.py temporarily, along with your index.html.
https://claude.ai/share/9485b631-bc30-4606-8e16-da5940993260
"""
 
import asyncio
import network
import time
 
SSID     = "BlackLock"
PASSWORD = "BlackLock2"
 
# ── Shared state ──────────────────────────────────────────────────────────────
state = {
    "start_time":       None,
    "duration":         None,
    "session_active":   False,
    "emergency_unlock": False,
    "message":          "",
    "schedule_info":    "",
}
 
# ── Access point ──────────────────────────────────────────────────────────────
def create_ap(ssid, password):
    ap = network.WLAN(network.AP_IF)
    ap.config(ssid=ssid, password=password)
    ap.active(True)
    timeout = 10
    while not ap.active() and timeout > 0:
        time.sleep(0.5)
        timeout -= 1
    if ap.active():
        ip, _ = ap.ipconfig('addr4')
        print(f"AP ready  →  SSID: {ssid}  PW: {password}  IP: {ip}")
        return ip
    print("AP failed to start")
    return None
 
# ── URL helpers ───────────────────────────────────────────────────────────────
def parse_query(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params
 
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
 
# ── HTML ──────────────────────────────────────────────────────────────────────
def render_page(message="", schedule_info=""):
    start = state["start_time"] or ""
    dur   = str(state["duration"]) if state["duration"] else ""
    try:
        with open("index.html", "r") as f:
            html = f.read()
    except OSError:
        html = "<h1>Error: index.html not found on Pico</h1>"
        return html
    html = html.replace("{{START_TIME}}",    start)
    html = html.replace("{{DURATION}}",      dur)
    html = html.replace("{{MESSAGE}}",       message)
    html = html.replace("{{SCHEDULE_INFO}}", schedule_info)
    return html
 
# ── Web server ────────────────────────────────────────────────────────────────
async def process_requests(reader, writer):
    try:
        request_line = await reader.readline()
        request_line = str(request_line, "utf-8").strip()
        print("REQ:", request_line)
 
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", b"\n"):
                break
 
        if not request_line or "favicon" in request_line:
            writer.write(b"HTTP/1.0 204 No Content\r\n\r\n")
            await writer.drain()
            await writer.wait_closed()
            return
 
        parts = request_line.split()
        if len(parts) < 2:
            await writer.wait_closed()
            return
 
        path = parts[1]
        route, qs = (path.split("?", 1) if "?" in path else (path, ""))
        params = parse_query(qs)
        message = ""
 
        # ── /set ─────────────────────────────────────────────────────────
        if route == "/set":
            st  = params.get("start_time", "").strip()
            dur = params.get("duration",   "").strip()
            if st and dur and dur.isdigit() and int(dur) > 0:
                state["start_time"] = st
                state["duration"]   = int(dur)
                message = f"✅ Schedule saved! Start: {st}, Duration: {dur} min"
                state["schedule_info"] = message
                state["message"]       = message
                print(f"Schedule set → start={st}, duration={dur}min")
            else:
                message = "❌ Invalid input. Please enter a valid time and duration."
 
        # ── /command ──────────────────────────────────────────────────────
        elif route == "/command":
            cmd = params.get("cmd", "")
            if cmd == "EMERGENCY_UNLOCK":
                state["emergency_unlock"] = True
                message = "🔓 Emergency unlock triggered!"
                state["message"] = message
                print("EMERGENCY UNLOCK received")
            elif cmd == "LOCK":
                message = "🔒 Lock command received."
                print("LOCK command received")
            else:
                message = f"Unknown command: {cmd}"
 
        # ── / (root) ──────────────────────────────────────────────────────
        else:
            message = state["message"]
 
        html = render_page(message, state["schedule_info"])
        response = (
            "HTTP/1.0 200 OK\r\n"
            "Content-Type: text/html\r\n"
            f"Content-Length: {len(html)}\r\n"
            "\r\n" + html
        )
        writer.write(response.encode())
        await writer.drain()
        print("Response sent.")
 
    except Exception as e:
        print("Request error:", e)
    finally:
        await writer.wait_closed()
        print("Client disconnected\n")
 
# ── Heartbeat (proves the event loop is alive) ────────────────────────────────
async def heartbeat():
    while True:
        print("[ heartbeat — event loop running ]")
        await asyncio.sleep(10)
 
# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    ip = create_ap(SSID, PASSWORD)
    if not ip:
        print("AP failed. Halting.")
        return
 
    print("\n--- BlackLock Connection Test ---")
    print(f"Connect your phone/laptop to Wi-Fi: '{SSID}'  password: '{PASSWORD}'")
    print(f"Then open a browser and go to:  http://{ip}/\n")
 
    server = await asyncio.start_server(process_requests, "0.0.0.0", 80)
    print("Web server started. Waiting for connections...")
 
    await asyncio.gather(
        heartbeat(),
    )
 
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped by user")
    except Exception as e:
        print("Fatal error:", e)