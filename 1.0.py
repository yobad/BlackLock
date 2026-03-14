"""
This code makes the load cell continuously read the weight that is placed above it,
while printing it out. If the weight placed on it is above a certain threshold, the stepper
motor will be triggered to close the door of the box. After a certain time has elapsed
(study session length), the motor will be triggered again in the opposite direction to open the
door.

This code uses the HX711 amplifier library created by Robert Hammelrath, which can be found in the
following link: https://github.com/robert-hh/hx711
It also uses a stepper motor library created by Lars Kellog-Stedman, which can be found in the
following link: https://github.com/larsks/micropython-stepper-motor

"""




import time
from hx711_gpio import HX711
from machine import Pin
import motor

sf = 1138.6597 #Change after calibrating
time_closed = 5 #Set by timer, 5 sec used as example
steps_to_close = 1028 #To be tweaked during testing

#Load cell pins
data_pin = Pin(5, Pin.IN, pull=Pin.PULL_DOWN)
clock_pin = Pin(6,Pin.OUT)

#Stepper motor pins
IN1 = 14
IN2 = 12
IN3 = 13
IN4 = 15

#Weight threshold (to be tweaked)
threshold = 170

motor_triggered = False

#Setting up load cell
hx711 = HX711(clock_pin, data_pin)
hx711.tare()
hx711.set_scale(sf)

#Setting up stepper motor
stepper_motor = motor.FullStepMotor.frompins(IN1, IN2, IN3, IN4)

while True:
    weight = hx711.get_units()
    print(f"{weight:.1f} grams")

    if weight >= threshold and motor_triggered is False:
        time.sleep(2) #2 second pause before closing door
        stepper_motor.step(steps_to_close)
        motor_triggered = True
        
        time.sleep(time_closed)

        stepper_motor.step(-(steps_to_close))

    if weight < threshold:
        motor_triggered = False

