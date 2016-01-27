import os, codecs
import pandas as pd, numpy as np

class CircleData(object):
    def __init__(self, mac):
        self.mac = mac
        self.log = pd.Series()
        self.slow_log = pd.Series()
        self.current_consumption = 0

class EnergyData(object):
    def __init__(self, log_path, slow_log_path, slow_interval=10*60, fast_interval=10):
        # self.log_path = log_path
        # self.slow_log_path = log_path
        self.circles = {}

        self.load_logfiles(log_path, slow_log=False)
        self.load_logfiles(slow_log_path, slow_log=True)

        for c in self.circles.values():
            if not c.log.index.sort_values().identical(c.log.index):
                print("log-index not sorted")
                c.log.sort_index(inplace=True)
            if not c.slow_log.index.sort_values().identical(c.slow_log.index):
                print("slow-log-index not sorted")
                c.slow_log.sort_index(inplace=True)

    def circle(self, mac):
        if len(mac) > 6:
            mac = mac[-6:]
        if mac in self.circles:
            return self.circles[mac]
        else:
            c = CircleData(mac)
            self.circles[mac] = c
            return c

    def load_logfiles(self, path, slow_log):

        item = 2 if slow_log else 1
        files = [ f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)) and f.endswith('.log') ]
        for fname in files:
            mac = fname[-10:-4]
            circle = self.circle(mac)
            f = codecs.open(path+fname, encoding="latin-1")
            lines = [line.strip().split(',') for line in f.readlines()]
            series = pd.Series([np.float(l[item]) for l in lines], index=[pd.Timestamp(l[0]) for l in lines])
            if slow_log:
                circle.slow_log = series
            else:
                circle.log = series

    def add_value(self, mac, timestamp, value, slow_log):
        c = self.circle(mac)
        if slow_log:
            log = c.slow_log
        else:
            log = c.log
            c.current_consumption = value
        log[pd.Timestamp(timestamp)] = value
        #todo: recalc accumulated consumption

    def report_offline(self, mac, timestamp):
        c = self.circle(mac)
        c.current_consumption = 0

    def current_consumption(self):
        return sum(c.current_consumption for c in self.circles.values())