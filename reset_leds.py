import sys
from time import sleep

sys.path.append('/home/plant/plantlight/plantlight/rsb/remotePlantAPI')
from remotePlantAPI import PlantAPI
plant = PlantAPI('/dev/ttyACM0', 9600)

plant.ledShiftRangeFromCurrent(1,17,0,0,0, 500)
