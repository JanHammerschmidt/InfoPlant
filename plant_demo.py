import sys, os
import numpy as np
from time import sleep
from datetime import datetime
from misc import linear_interp_color, linear_interp, TouchLimiter

sys.path.append('/home/plant/plantlight/plantlight/rsb/remotePlantAPI')
from remotePlantAPI import PlantAPI
plant = PlantAPI('/dev/ttyACM0', 9600)


def dc(v,t=8,s=-1): # daily (past 24h) consumption
    if v != -2:
        v = np.clip(v,-1,1)
    twigs = [(3, (6, 8, 12, 16)), (1, (9, 10, 13, 17)), (4, (9, 10, 13, 17)), (2, (10, 9, 5, 2))]
    if s != -1:
        twigs = [t for t in twigs if t[0] == s]
    for i,(stop,low,mid,high) in twigs:
        if v > 0:
            d = linear_interp(mid,high,v)
        elif v == -2:
            d = stop
        else:
            d = linear_interp(mid,low,-v)
        d = int(round(d))
        plant.tugDegree(i, d)
        if i < len(twigs):
            sleep(t)


def plant_map2color(v):
    green = (40,120,0)
    red = (255,0,0)
    yellow = (255,220,0)
    v = np.clip(v,-1,1)
    if v > 0:
        color = linear_interp_color(yellow, red, v)
    else:
        color = linear_interp_color(yellow, green, -v)
    return tuple([int(round(i)) for i in color]) # round to integer

def plant_set_color(c=(0,0,0), t=1000):
    plant.ledShiftRangeFromCurrent(1,17,c[0],c[1],c[2],t)


def cc(v): #current consumption
    if v == 2:
        plant.ledPulseRange(1,17,255,0,0,500)
    else:
        plant_set_color(plant_map2color(v))

def touch_callback():
    os.system('mpg123 /home/plant/r2d2_short.mp3 &')
    touch_limiter.callback = touch_callback2

def touch_callback2():
    os.system('mpg123 /home/plant/electricity4.mp3 &')
    touch_limiter.callback = touch_callback

touch_limiter = TouchLimiter(touch_callback,999)

def touch_limiter_callback():
    touch_limiter.touch(datetime.now())

def sound():
    plant.touch_callback = touch_limiter_callback



