import machine
import utime
import sdcard
import vfs
import os

# Mount SD card
def mount_sd():
    try:
        cs = machine.Pin(17, machine.Pin.OUT)
        spi = machine.SPI(0,
                          baudrate=1_000_000,
                          polarity=0,
                          phase=0,
                          bits=8,
                          firstbit=machine.SPI.MSB,
                          sck=machine.Pin(18),
                          mosi=machine.Pin(19),
                          miso=machine.Pin(16))
        sd = sdcard.SDCard(spi, cs)
        vfs_obj = vfs.VfsFat(sd) # type: ignore
        vfs.mount(vfs_obj, "/sd")
        print("SD card mounted successfully")
        print("Files on SD card:", os.listdir("/sd"))
        return True
    except Exception as e:
        print(f"Failed to mount SD card: {e}")
        return False

# Setup PWM on GP22
audio_pin = machine.PWM(machine.Pin(22))
audio_pin.freq(500000)  # 500kHz PWM frequency for better audio quality

def parse_wav_header(f):
    # Read WAV header
    header = f.read(44)
    if len(header) < 44 or header[0:4] != b'RIFF' or header[8:12] != b'WAVE':
        raise ValueError("Invalid WAV file")
    
    # Get sample rate (little-endian)
    sample_rate = int.from_bytes(header[24:28], 'little')
    # Get number of channels
    channels = int.from_bytes(header[22:24], 'little')
    # Get bits per sample
    bits_per_sample = int.from_bytes(header[34:36], 'little')
    
    return sample_rate, channels, bits_per_sample

def play_wav(filename):
    try:
        with open(filename, "rb") as f:
            sample_rate, channels, bits_per_sample = parse_wav_header(f)
            
            print(f"WAV info: {sample_rate}Hz, {channels} channels, {bits_per_sample} bits")
            
            if bits_per_sample != 8:
                raise ValueError("Only 16-bit WAV files supported")
            
            # Calculate delay between samples
            delay_us = int(1000000 / sample_rate)
            print(f"Delay: {delay_us}us")
            
            # Read the file in chunks
            chunk_size = 1024
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                
                # Process samples
                bytes_per_sample = bits_per_sample // 8 * channels
                for i in range(0, len(data), bytes_per_sample):
                    if i + bytes_per_sample <= len(data):
                        if channels == 1:
                            # Mono
                            sample = int.from_bytes(data[i:i+2], 'little')
                            if sample >= 32768:
                                sample -= 65536
                        else:
                            # Stereo: average left and right
                            left = int.from_bytes(data[i:i+2], 'little')
                            if left >= 32768:
                                left -= 65536
                            right = int.from_bytes(data[i+2:i+4], 'little')
                            if right >= 32768:
                                right -= 65536
                            sample = (left + right) // 2
                        
                        # Convert to unsigned 16-bit for PWM
                        duty = sample + 32768
                        audio_pin.duty_u16(duty)
                        
                        # Delay to match sample rate
                        utime.sleep_us(delay_us)
    
    except Exception as e:
        print(f"Error playing WAV: {e}")
    
    finally:
        audio_pin.duty_u16(32768)  # Set to midpoint (silence)

# Play your file
if mount_sd():
    play_wav("/sd/OPencouragement.wav")
else:
    print("Cannot play WAV: SD card not mounted")