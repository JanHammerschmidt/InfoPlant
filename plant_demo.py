import sys
import numpy as np
from time import sleep
from misc import linear_interp_color, linear_interp

sys.path.append('/home/plant/plantlight/plantlight/rsb/remotePlantAPI')
from remotePlantAPI import PlantAPI
plant = PlantAPI('/dev/ttyACM0', 9600)


def dc(v,t=8): # daily (past 24h) consumption
    v = np.clip(v,-1,1)
    twigs = [(3,(6,8,12,16)),(1,(6,8,11,14)),(4,(5,7,10,14)),(2,(7,9,12,15))]
    for i,(stop,low,mid,high) in twigs:
        if v > 0:
            d = linear_interp(mid,high,v)
        elif v == -1:
            d = stop
        else:
            d = linear_interp(mid,low,-v)
        d = int(round(d))
        plant.tugDegree(i, d)
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
