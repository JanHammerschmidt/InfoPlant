print("load dependencies")
from EnergyData import EnergyData, init_matplotlib
from plotly_plot import init_plotly
from os import path
import pandas as pd
import numpy as np
import json

def load_data(dir):
    global data
    circle_from_mac =  {'78DB3F':0, '8FB7BB':1, '8FB86B':2, '8FD194':3,
                        '8FD25D':4, '8FD2DE':5, '8FD33A':6, '8FD358':7, '8FD472':8,
                        "78EE23":0, "8FB663":1, "8FBB68":2, "8FB6CB":3, "8FD32C":4,
                        "8FD34A":5, "8FD4F9":6, "8FD319":7, "8FD1A6":8}

    session = json.load(open(path.join(dir,'datalog','session.json')))
    start_time = pd.Timestamp(session['start'])
    data = EnergyData(circle_from_mac, path.join(dir,'datalog'), path.join(dir,'slow_log'), dir, dir, start_time, False)

def plot_daily():
    ts = [data.interval2timestamp(i) for i in range(len(data.intervals))] #timestamps of intervals
    day_starts = [i for i,t in enumerate(ts) if t.hour == 0 and t.minute == data.interval_length]
    x = [ts[i] for i in day_starts]
    y = [sum(data.intervals[i:i+data.intervals_per_day])/24 for i in day_starts[1:-1]] # everything except the first and last day
    y.insert(0, sum(data.intervals[:day_starts[0]]) / day_starts[0] * data.intervals_per_hour)
    y.append(sum(data.intervals[day_starts[-1]:]) / (len(data.intervals)-day_starts[-1]) * data.intervals_per_hour)
    plt.bar(range(len(y)),y)
    plt.xticks(range(len(x)),[i.strftime('%a, %d.%m') for i in x])

def plot_cmp():
    energy_data = data
    energy_data.calc_avg_consumption_per_interval()
    energy_data.calculate_std()
    print(energy_data.std, energy_data.std_intervals)
    energy_data.smooth_avg_consumption()
    idx = len(energy_data.intervals)-1
    i0 = idx-energy_data.intervals_per_day
    while True:
        i = idx
        while i >= i0:
            for j in range(6):
                i -= 1
                energy_data.plot_data(i, False)
            energy_data.plot_data(i, True)


plt = init_matplotlib()
load_data('/Users/jhammers/Dropbox/Eigene Dateien/phd/Projekte/1_InfoPlant Evaluation/data/1_3')

# init_plotly()
# data.plot_plotly()

plot_cmp()

# plot_daily()
# plt.show()