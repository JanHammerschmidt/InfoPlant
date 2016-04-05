import sys
from time import sleep

sys.path.append('/home/plant/plantlight/plantlight/rsb/remotePlantAPI')
from remotePlantAPI import PlantAPI
plant = PlantAPI('/dev/ttyACM0', 9600)


for i,v in enumerate([6,7,6,5]):
    plant.tugDegree(i+1, v)
    sleep(7)
    