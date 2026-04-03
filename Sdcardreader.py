import machine
import sdcard
import vfs

# CS pin location
cs = machine.Pin(17, machine.Pin.OUT)

# Initialize SPI peripheral (start with 1 MHz)
spi = machine.SPI(0,
                  baudrate=1_000_000,
                  polarity=0,
                  phase=0,
                  bits=8,
                  firstbit=machine.SPI.MSB,
                  sck=machine.Pin(18),
                  mosi=machine.Pin(19),
                  miso=machine.Pin(16))

# Initialize SD card
sd = sdcard.SDCard(spi, cs)

# Mount the filesystem
vfs_obj = vfs.VfsFat(sd)  # type: ignore
vfs.mount(vfs_obj, "/sd")

# Create a file and write to it
with open("/sd/test.txt", "w") as f:
    f.write("Hello, SD card!")

# Read the file back
with open("/sd/test.txt", "r") as f:
    content = f.read()
    print(content)