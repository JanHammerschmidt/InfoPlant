from bisect import bisect_left, bisect_right
from math import ceil
from sys import stdout
from os import path
import os, codecs, json, matplotlib
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
timedelta = pd.offsets.timedelta

matplotlib.style.use('ggplot')

class ProgressBar:
    def __init__(self, name, nitems):
        self.name = name
        self.c = 0
        self.nitems = nitems
        if nitems > 0:
            self.out()
        else:
            self.nitems = 1
            self.next()

    def out(self):
        stdout.write('\r')
        stdout.write("%s [%-20s] %d%%" % (self.name, '='*(self.c*20/self.nitems), self.c * 100/self.nitems))
        stdout.flush()

    def next(self):
        self.c += 1
        self.out()
        if (self.c >= self.nitems):
            stdout.write('\n')

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
                return d[last] * dt / 3600 if dt > 0 else 0
            else:
                return d[last] * dt / 3600

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
        return acc / 3600 # convert from Ws to Wh

    def accumulated_consumption(self, begin, end):
        # we disregard the first slow measurement after a gap here
        if begin >= end:
            return 0
        d,index = self.slow_log, self.slow_log.index
        first = bisect_left(index, begin)
        last = bisect_right(index, end)
        # print(first,last)
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

    def calc_avg_consumption_per_interval(self, intervals_per_day, intervals_offset):
        consumptions = [[] for _ in range(intervals_per_day)]
        for i,c in enumerate(self.intervals, start=intervals_offset):
            consumptions[i % intervals_per_day].append(c)
        self.consumption_per_interval = [0] * intervals_per_day
        for i,c in enumerate(consumptions):
            self.consumption_per_interval[i] = sum(c) / len(c)

class EnergyData(object):
    def __init__(self, log_path, slow_log_path, cache_path, start_time, first_run, slow_interval=10*60, fast_interval=10):
        self.cache_fname = path.join(cache_path, 'energy_data.json')
        self.slow_interval = slow_interval
        self.fast_interval = fast_interval
        self.circles = {}

        self.load_logfiles(log_path, slow_log=False)
        self.load_logfiles(slow_log_path, slow_log=True)

        self.interval_length = 10 # minutes
        self.interval_length_s = self.interval_length * 60
        self.interval_td = timedelta(minutes=self.interval_length)
        self.intervals_per_day = 24*60/self.interval_length
        self.intervals_start = start_time.replace(second=0,microsecond=0, minute=start_time.minute - start_time.minute % self.interval_length) + timedelta(minutes=2 * self.interval_length)
        self.day_start = self.intervals_start.replace(hour=4,minute=0)
        self.intervals_offset = int((self.intervals_start - self.day_start).total_seconds()) / self.interval_length_s
        if self.intervals_offset < 0:
            self.intervals_offset += self.intervals_per_day

        last_t = start_time
        for c in self.circles.values():
            if not c.log.index.sort_values().identical(c.log.index):
                print("log-index not sorted")
                c.log = c.log[~c.log.index.duplicated(keep='first')]
                c.log.sort_index(inplace=True)
            if not c.slow_log.index.sort_values().identical(c.slow_log.index):
                print("slow-log-index not sorted")
                c.slow_log = c.slow_log[~c.slow_log.index.duplicated(keep='first')]
                c.slow_log.sort_index(inplace=True)
            if len(c.log) > 0:
                last_t = max(last_t, c.log.index[-1])
            if len(c.slow_log) > 0:
                last_t = max(last_t, c.slow_log.index[-1])

        # self.last_t = last_t

        n_intervals = int(ceil((last_t - start_time).total_seconds() / (self.interval_length_s)))
        if self.load_cache():
            p = ProgressBar('re-analyze last 24 hours', min(n_intervals, self.intervals_per_day))
            self.resize_intervals(n_intervals)
            for i in range(max(len(self.intervals)-self.intervals_per_day, 0), len(self.intervals)):
                self.update_interval(i)
                p.next()
        else:
            self.intervals = [0] * n_intervals
            for c in self.circles.values():
                c.intervals = self.intervals[:]

            if (len(self.intervals) > 0):
                p = ProgressBar('analyzing historic consumption', len(self.intervals))
                for i in range(len(self.intervals)):
                    self.update_interval(i)
                    p.next()
            if not first_run:
                self.save_cache()

        self.consumption_per_interval = [0] * self.intervals_per_day
        self.calc_avg_consumption_per_interval()

        # pwiddict =  {'78DB3F':1, '8FB7BB':2, '8FB86B':3, '8FD194':4,
        #              '8FD25D':5, '8FD2DE':6, '8FD33A':7, '8FD358':8, '8FD472':9}
        # locdict =   {1:'Flur',   2:'Wohn/TV', 3:'Wohn/Lm', 4:'Wohn/Le',
        #              5:'Bad', 6:'Kuech/L', 7:'Kuech/MW', 8:'Toaster', 9:'TH'}
        #
        # x = [self.intervals_start + timedelta(minutes=self.interval_length * i) for i in range(len(self.intervals))]
        # x2 = [self.day_start + timedelta(minutes=self.interval_length * i) for i in range(self.intervals_per_day)]
        # plt.plot(x2, np.cumsum(self.consumption_per_interval))
        # plt.show()
        #
        # for k in range(9):
        #     mac = (key for key,value in pwiddict.items() if value==k+1).next()
        #     c = self.circle(mac)
        #     if True:
        #         c.calc_avg_consumption_per_interval(self.intervals_per_day, self.intervals_offset)
        #         plt.plot(x2, c.consumption_per_interval)
        #     else:
        #         plt.plot(x, np.cumsum(c.intervals))
        #     plt.title(locdict[k+1])
        #     plt.show()

        self.intervals_dirty = set()

    def update_intervals(self):
        if len(self.intervals_dirty) > 0:
            maxi = max(self.intervals_dirty)
            self.resize_intervals(maxi+1)
            for i in self.intervals_dirty:
                self.update_interval(i)
        # self.save_cache()

    def resize_intervals(self, n):
        if n > len(self.intervals):
            self.intervals += [0] * (n - len(self.intervals))
            for c in self.circles.values():
                c.intervals += [0] * (n - len(c.intervals))

    def update_interval(self, i):
        t = self.intervals_start + timedelta(minutes=i*self.interval_length)
        self.intervals[i] = self.accumulated_consumption(t - self.interval_td, t, i)

    def update_start_interval(self):
        dsi = self.day_start_interval(len(self.intervals)-1)
        ret = dsi != self.current_start_interval
        self.current_start_interval = dsi
        return ret

    def current_daily_consumption(self):
        return sum(self.intervals[self.current_start_interval:])

    def plot_current_and_historic_consumption(self):
        plt.ion()
        plt.clf()
        x = [self.day_start + timedelta(minutes=self.interval_length * i) for i in range(self.intervals_per_day)]
        plt.plot(x, np.cumsum(self.consumption_per_interval), label='historic consumption', color='blue')

        idx = len(self.intervals)-1
        i0 = self.day_start_interval(idx)
        if i0 > 0 and idx-i0 > 0:
            plt.plot(x[:idx-i0], np.cumsum(self.intervals[i0:-1]), label='current consumption', color='red')
            # plt.plot(x[:idx-i0+1], self.intervals[i0:], label='current consumption')
        plt.legend(loc='best')
        plt.pause(0.001)
        # plt.show()

    def accumulated_consumption(self, begin, end, i): # for an interval
        t = []
        for c in self.circles.values():
            cc = c.accumulated_consumption(begin, end)
            c.intervals[i] = cc
            t.append(cc)
        return sum(t)
        # return sum(c.accumulated_consumption(begin, end) for c in self.circles.values())

    def calc_avg_consumption_per_interval(self):
        consumptions = [[] for _ in range(self.intervals_per_day)]
        for i,c in enumerate(self.intervals, start=self.intervals_offset):
            consumptions[i % self.intervals_per_day].append(c)
        for i,c in enumerate(consumptions):
            self.consumption_per_interval[i] = sum(c) / len(c)

    def load_cache(self):
        try:
            cache = json.load(open(self.cache_fname))
            intervals = cache['intervals']
            circle_intervals = cache['circle_intervals']
            if pd.Timestamp(cache['intervals_start']) != self.intervals_start or cache['intervals_offset'] != self.intervals_offset:
                print("cache doesn't seem to match current config! => discarding..")
        except Exception:
            return False
        self.intervals = intervals
        for mac,intv in circle_intervals.items():
            self.circle(mac).intervals = intv
        print("successfully loaded cache")
        return True

    def save_cache(self):
        cache = {'intervals_start': self.intervals_start.isoformat(), 'intervals_offset': self.intervals_offset}
        cache['intervals'] = self.intervals
        cache['circle_intervals'] = {c.mac: c.intervals for c in self.circles.values()}
        with open(self.cache_fname, 'w') as f:
            json.dump(cache, f, default=lambda o: o.__dict__)

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
        p = ProgressBar('loading logfiles' + (' (interval data)' if slow_log else ''), len(files))
        for fname in files:
            mac = fname[-10:-4]
            circle = self.circle(mac)
            f = codecs.open(path+fname, encoding="latin-1")
            lines = [line.strip().split(',') for line in f.readlines()]
            series = pd.Series([np.max(np.float(l[item]),0) for l in lines], index=[pd.Timestamp(l[0]) for l in lines])
            if slow_log:
                circle.slow_log = circle.slow_log.append(series)
            else:
                circle.log = circle.log.append(series)
            p.next()

    def timestamp2interval(self, ts):
        return int(ceil((ts - self.intervals_start).total_seconds() / self.interval_length_s))

    def interval2timestamp(self, i):
        return self.intervals_start + timedelta(minutes = i * self.interval_length)

    def day_start_interval(self, i):
        return (i/self.intervals_per_day) * self.intervals_per_day - self.intervals_offset

    def add_value(self, mac, timestamp, value, slow_log):
        c = self.circle(mac)
        if slow_log:
            log = c.slow_log
        else:
            log = c.log
            c.current_consumption = value
        ts = pd.Timestamp(timestamp)
        log[ts] = value
        # flag intervals of accumulated consumption for recalculation
        i = self.timestamp2interval(ts)
        ts0 = ts - timedelta(seconds = self.slow_interval if slow_log else self.fast_interval)
        self.intervals_dirty.update(range(self.timestamp2interval(ts0), i+1))
        # self.invalidate_cache()

    def report_offline(self, mac, timestamp):
        self.circle(mac).current_consumption = 0

    def current_consumption(self):
        return sum(c.current_consumption for c in self.circles.values())

    def current_accumulated_daily_consumption(self):
        return sum(self.intervals[self.current_start_interval:])

    def invalidate_cache(self):
        if os.path.isfile(self.cache_fname):
            os.remove(self.cache_fname)
