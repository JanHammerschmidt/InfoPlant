import sys
import numpy as np
import os
from misc import linear_interp_color

cfg_plant = True

if cfg_plant:
    global plant
    sys.path.append('/home/plant/plantlight/plantlight/rsb/remotePlantAPI')
    from remotePlantAPI import PlantAPI
    plant = PlantAPI('/dev/ttyACM0', 9600)


from time import sleep

def c(r,g,b,t=500):
    plant.ledShiftRangeFromCurrent(1,17,r,g,b,t)

def touch_callback():
    print("putschiii")

plant.touch_callback = touch_callback
plant.debug_prints = False

def disco():
    [plant.ledPulseSingle(i,abs(sin(i))*255,abs(cos(i))*255,abs(sin(i*2))*255,50+abs(cos(i*2))*150) for i in range(1,18)]

def reset():
    c(0,0,0)
    #plant.ledShiftRangeFromCurrent(1,17,0,0,0, 500)

def cc(c):
    c(c[0],c[1],c[2],500)

def plant_map2color(v):
    green = (0,255,0)
    red = (255,0,0)
    yellow = (255,220,0)
    v = np.clip(v,-1,1)
    if v > 0:
        return linear_interp_color(yellow, red, v)
    else:
        return linear_interp_color(yellow, green, -v)

def color_test():
    for i in np.linspace(-1,1,20):
        cc(plant_map2color(i))
        sleep(1)

def play_sound():
    os.system('mpg123 /home/plant/electricity4.mp3 &')

# ledShiftRange(0,17,255,0,0,0,0,0,5000)

reset()

# plant.resetPlant()

# color_test()

print('all done')