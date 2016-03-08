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
    def __init__(self, idx, fast_interval, slow_interval):
        self.idx = idx
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
    def __init__(self, circle_idx_by_mac, log_path, slow_log_path, cache_path, start_time, first_run, slow_interval=10*60, fast_interval=10):
        self.circle_idx_by_mac = circle_idx_by_mac
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
        print("intervals_start is: %s" % self.intervals_start.isoformat())
        self.day_start = self.intervals_start.replace(hour=4,minute=0)
        self.intervals_offset = int((self.intervals_start - self.day_start).total_seconds()) / self.interval_length_s
        if self.intervals_offset < 0:
            self.intervals_offset += self.intervals_per_day

        last_t = start_time
        for c in self.circles.values():
            c.log.sort_index(inplace=True)
            dupl = c.log.index.duplicated(keep='first')
            if True in dupl:
                print("log-index has %i duplicates! (for circle %i)" % (sum(dupl), c.idx))
                c.log = c.log[~dupl]

            c.slow_log.sort_index(inplace=True)
            dupl = c.slow_log.index.duplicated(keep='first')
            if True in dupl:
                print("slow_log-index has %i duplicates! (for circle %i)" % (sum(dupl), c.idx))
                c.slow_log = c.slow_log[~dupl]

            c.last_timestamp = self.intervals_start

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
        self.update_start_interval(False)

        self.intervals_dirty = set()

    def update_intervals(self):
        if len(self.intervals_dirty) > 0:
            maxi = max(self.intervals_dirty)
            if maxi >= 0:
                # print("maxi", maxi)
                self.resize_intervals(maxi+1)
                for i in self.intervals_dirty:
                    self.update_interval(i)
            self.intervals_dirty.clear()

    def resize_intervals(self, n):
        if n > len(self.intervals):
            self.intervals += [0] * (n - len(self.intervals))
            for c in self.circles.values():
                c.intervals += [0] * (n - len(c.intervals))

    def update_interval(self, i):
        if i >= 0:
            t = self.intervals_start + timedelta(minutes=i*self.interval_length)
            self.intervals[i] = self.accumulated_consumption(t - self.interval_td, t, i)

    def update_start_interval(self, check = True):
        dsi = self.day_start_interval(len(self.intervals)-1)
        ret = (dsi != self.current_start_interval) if check else True
        self.current_start_interval = dsi
        if ret:
            self.day_start = self.interval2timestamp(dsi)
            print("new day_start: %s" % self.day_start.isoformat())
        return ret

    def plot_current_and_historic_consumption(self):
        plt.ion()
        plt.clf()
        x = [self.day_start + timedelta(minutes=self.interval_length * i) for i in range(self.intervals_per_day)]
        plt.plot(x, np.cumsum(self.consumption_per_interval), label='historic consumption', color='blue')

        idx = len(self.intervals)-1
        i0 = self.day_start_interval(idx)
        if i0 > 0 and idx-i0 > 0:
            plt.plot(x[:idx-i0+1], np.cumsum(self.intervals[i0:i0+len(x)]), label='current consumption', color='red')
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
            if len(c) > 0:
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
        for idx,intv in circle_intervals.items():
            self.circle(int(idx)).intervals = intv
        print("successfully loaded cache")
        return True

    def save_cache(self):
        cache = {'intervals_start': self.intervals_start.isoformat(), 'intervals_offset': self.intervals_offset}
        cache['intervals'] = self.intervals
        cache['circle_intervals'] = {c.idx: c.intervals for c in self.circles.values()}
        with open(self.cache_fname, 'w') as f:
            json.dump(cache, f, default=lambda o: o.__dict__)

    def circle(self, idx):
        if idx in self.circles:
            return self.circles[idx]
        else:
            c = CircleData(idx, self.fast_interval, self.slow_interval)
            self.circles[idx] = c
            return c

    def circle_from_mac(self, mac):
        return self.circle(self.circle_idx_by_mac[mac])

    def load_logfiles(self, path, slow_log):
        item = 2 if slow_log else 1
        files = [ f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)) and f.endswith('.log') ]
        p = ProgressBar('loading logfiles' + (' (interval data)' if slow_log else ''), len(files))
        for fname in files:
            mac = fname[-10:-4]
            circle = self.circle_from_mac(mac)
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
        return (i/self.intervals_per_day) * self.intervals_per_day - self.intervals_offset + \
               (self.intervals_per_day if i%self.intervals_per_day > (self.intervals_per_day - self.intervals_offset) else 0)

    def add_value(self, mac, timestamp, value, slow_log, value_1s = None):
        c = self.circle_from_mac(mac)
        if slow_log:
            log = c.slow_log
        else:
            c.current_consumption = value if value_1s < 200 else value_1s
            if (timestamp - c.last_timestamp).total_seconds() < 8:
                return False
            c.last_timestamp = timestamp
            log = c.log
        ts = pd.Timestamp(timestamp)
        log[ts] = value
        # flag intervals of accumulated consumption for recalculation
        i = self.timestamp2interval(ts)
        ts0 = ts - timedelta(seconds = self.slow_interval if slow_log else self.fast_interval)
        self.intervals_dirty.update(range(self.timestamp2interval(ts0), i+1))
        return True

    def report_offline(self, mac, timestamp):
        self.circle_from_mac(mac).current_consumption = 0

    def current_consumption(self):
        return sum(c.current_consumption for c in self.circles.values())

    def current_accumulated_daily_consumption(self):
        return sum(self.intervals[max(self.current_start_interval,0):])

    def current_accumulated_consumption_24h(self):
        return sum(self.intervals[-self.intervals_per_day:])

    def comparison_avg_accumulated_daily_consumption(self, ts):
        total_seconds = (ts - self.day_start).total_seconds()
        full_intervals = min(int(total_seconds / self.interval_length_s), self.intervals_per_day)
        part_interval = (total_seconds % self.interval_length_s) / self.interval_length_s
        consumption = sum(self.consumption_per_interval[:full_intervals])
        if full_intervals < self.intervals_per_day:
            consumption += part_interval * self.consumption_per_interval[full_intervals]
        return consumption

    def comparison_avg_accumulated_consumption_24h_2(self, ts):
        consumption = sum(self.consumption_per_interval) # avg total consumption
        idx = self.timestamp2interval(ts) # index of current interval
        i = (idx+self.intervals_offset) % self.intervals_per_day # this is the idx of the "current" comparison interval that is being filled up
        part_interval = (self.interval2timestamp(idx) - ts).total_seconds() / self.interval_length_s
        return consumption - part_interval * self.consumption_per_interval[i]

    def comparison_avg_accumulated_consumption_24h(self, ts):
        current_interval = len(self.intervals)-1
        interval_ts = self.interval2timestamp(current_interval) # timestamp of current interval
        consumption = sum(self.consumption_per_interval)
        if ts > interval_ts: # current time is 'beyond' intervals => all intervals are filled up => use full comparison data
            return consumption
        else:
            cmp_interval = (current_interval+self.intervals_offset) % self.intervals_per_day # this is the idx of the current *comparison* interval that is being filled up
            part_sec = (interval_ts - ts).total_seconds()
            if part_sec > self.interval_length_s: # ts is before the current interval => shouldn't happen!
                print("!!warning: ts before current interval")
            part = part_sec / self.interval_length_s
            return consumption - part * self.consumption_per_interval[cmp_interval]

    def calculate_std(self):
        # from 6:00 to 1:00
        start = (6-4)*60*60 / self.interval_length_s - self.intervals_offset
        end = (25-4)*60*60 / self.interval_length_s - self.intervals_offset
        v = []
        for i in range(start,end): # i: end-time of a day-interval
            for i1 in range(i,len(self.intervals),self.intervals_per_day): # check all possible end-times
                i0 = i1 - self.intervals_per_day # i0: start of the day-interval
                if i0 >= 0: # within measured time?
                    v.append(sum(self.intervals[i0:i1])) # 24h consumption
        self.std = np.std(v)
