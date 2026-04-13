import machine
import utime
import os
import sdcard
import vfs

# 1. Setup PWM (The Output)
# We postpone creation of PWM until playback so idle is silent
audio_pin = None

# 2. SD Card Setup (Keep your existing wiring)
def mount_sd():
    try:
        cs = machine.Pin(17, machine.Pin.OUT)
        spi = machine.SPI(0, baudrate=1000000, sck=machine.Pin(18), mosi=machine.Pin(19), miso=machine.Pin(16))
        sd = sdcard.SDCard(spi, cs)
        vfs.mount(vfs.VfsFat(sd), "/sd") # type: ignore
        print("SD mount OK")
        print("/sd files:", os.listdir("/sd"))
        return True
    except Exception as e:
        print("SD mount failed:", e)
        return False

def play_wav_8bit(filename):
    global audio_pin
    audio_pin = machine.PWM(machine.Pin(22))
    audio_pin.freq(20000)  # 16kHz carrier

    try:
        with open(filename, "rb") as f:
            # Read header and set timing properly
            hdr = f.read(44)
            if len(hdr) < 44 or hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
                raise ValueError("Not a WAV file")

            sample_rate = int.from_bytes(hdr[24:28], "little")
            delay_us = int(500000 / sample_rate)
            print("sample_rate", sample_rate, "delay_us", delay_us)

            # Using 8-bit WAV data (0..255)
            while True:
                chunk = f.read(100)
                if not chunk:
                    break
                for byte in chunk:
                    audio_pin.duty_u16(byte * 257)
                    utime.sleep_us(delay_us)

    except Exception as e:
        print("Error:", e)
    finally:
        if audio_pin:
            audio_pin.duty_u16(0)  # Midpoint for silence
            audio_pin.deinit()
            audio_pin = None

if mount_sd():
    play_wav_8bit("/sd/motivation2.wav")