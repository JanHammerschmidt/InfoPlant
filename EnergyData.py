from bisect import bisect_left, bisect_right
import os, codecs
import pandas as pd, numpy as np
timedelta = pd.offsets.timedelta

class CircleData(object):
    def __init__(self, mac, fast_interval, slow_interval):
        self.mac = mac
        self.log = pd.Series()
        self.slow_log = pd.Series()
        self.current_consumption = 0
        self.max_gap = fast_interval * 2.5
        self.max_slow_gap = slow_interval * 1.1
        self.td8 = timedelta(seconds=8)
        self.td10m = timedelta(minutes=10)

    def accumulated_consumption_fast(self, begin, end):
        if begin >= end:
            return 0
        d,index = self.log, self.log.index
        first = bisect_right(index, begin)
        if first >= len(index):
            return 0
        last = bisect_left(index, end)
        if last == 0:
            return 0
        if first == last:
            dt = (end-begin).total_seconds()
            gap = (index[last] - index[last-1]).total_seconds()
            if gap > self.max_gap:
                dt = ( end - max(index[last]-self.td8, begin) ).total_seconds()
                return d[last] * dt if dt > 0 else 0
            else:
                return d[last] * dt

        dt = (index[first]-begin).total_seconds()
        gap = (index[first]-index[first-1]).total_seconds() if first > 0 else 999
        if gap > self.max_gap:
            dt = min(dt, 8)
        acc = d[first] * dt

        for i in range(first+1,last):
            dt = (index[i]-index[i-1]).total_seconds()
            if dt > self.max_gap:
                dt = 8
            acc += d[i] * dt

        if last < len(index):
            dt = (end-index[last-1]).total_seconds()
            gap = (index[last]-index[last-1]).total_seconds()
            if gap > self.max_gap:
                dt = max((end - (index[last] - self.td8)).total_seconds(), 0)
            acc += d[last] * dt
        return acc

    def accumulated_consumption(self, begin, end):
        # we disregard the first slow measurement after a gap here
        if begin >= end:
            return 0
        d,index = self.slow_log, self.slow_log.index
        first = bisect_left(index, begin)
        last = bisect_right(index, end)
        print(first,last)
        if first == last or first >= len(index) or last == 0:
            return self.accumulated_consumption_fast(begin, end)

        acc = self.accumulated_consumption_fast(begin, index[first])
        for i in range(first+1, last):
            dt = (index[i] - index[i-1]).total_seconds()
            if dt > self.max_slow_gap:
                acc += self.accumulated_consumption_fast(index[i-1], index[i])
            else:
                acc += d[i]

        return acc + self.accumulated_consumption_fast(index[last-1], end)


class EnergyData(object):
    def __init__(self, log_path, slow_log_path, slow_interval=10*60, fast_interval=10):
        # self.log_path = log_path
        # self.slow_log_path = log_path
        self.slow_interval = slow_interval
        self.fast_interval = fast_interval
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
            # c.accumulated_consumption(c.slow_log.index[-4] + pd.offsets.timedelta(seconds=1), c.slow_log.index[-3] - pd.offsets.timedelta(seconds=1)) #in-between
            # c.accumulated_consumption(c.slow_log.index[0] - pd.offsets.timedelta(minutes=10), c.slow_log.index[0] - pd.offsets.timedelta(minutes=1)) # left
            c.accumulated_consumption(c.slow_log.index[-2] - pd.offsets.timedelta(minutes=1), c.slow_log.index[-1] + pd.offsets.timedelta(minutes=10)) # right
            c.accumulated_consumption(c.slow_log.index[5], c.slow_log.index[6]) # precisely on two log-points
            c.accumulated_consumption(c.slow_log.index[5], c.slow_log.index[7]) # same, but further away: 3)
            c.accumulated_consumption(c.slow_log.index[5], c.slow_log.index[5] + pd.offsets.timedelta(seconds=1)) # 4) end in-between (near)
            c.accumulated_consumption(c.slow_log.index[5] + pd.offsets.timedelta(seconds=1), c.slow_log.index[6]) # 4) same, but the other way around
            c.accumulated_consumption(c.slow_log.index[5], c.slow_log.index[7] + pd.offsets.timedelta(seconds=1)) # 4) same, but further away
            c.accumulated_consumption(c.slow_log.index[0] - pd.offsets.timedelta(seconds=1), c.slow_log.index[0]) # 6)  left (end on log-point)
            c.accumulated_consumption(c.slow_log.index[-1] - pd.offsets.timedelta(seconds=1), c.slow_log.index[-1]) # 7) right (end on log-point)
            c.accumulated_consumption(c.slow_log.index[-2] - pd.offsets.timedelta(seconds=1), c.slow_log.index[-1]) # 7) same, but further away
            c.accumulated_consumption(c.slow_log.index[-1] - pd.offsets.timedelta(seconds=3), c.slow_log.index[-1] - pd.offsets.timedelta(seconds=2)) # 7) in-between with gap

    def circle(self, mac):
        if len(mac) > 6:
            mac = mac[-6:]
        if mac in self.circles:
            return self.circles[mac]
        else:
            c = CircleData(mac, self.fast_interval, self.slow_interval)
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
        self.circle(mac).current_consumption = 0

    def current_consumption(self):
        return sum(c.current_consumption for c in self.circles.values())