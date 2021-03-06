from bisect import bisect_left, bisect_right
from math import ceil, sqrt
from sys import stdout
from os import path
from smooth import smooth
from plotly_plot import plot_plotly
from misc import ProgressBar
import os, codecs, json, dateutil
import pandas as pd, numpy as np
timedelta = pd.offsets.timedelta

cfg_circle_intervals = False
cfg_spike_consumption = 500


def init_matplotlib():
    stdout.write("init matplotlib..")
    global plt
    import matplotlib.pyplot as plt
    # import matplotlib
    plt.style.use('ggplot')
    print(" done")
    return plt


class CircleData(object):
    def __init__(self, idx, fast_interval, slow_interval, last_timestamp):
        self.idx = idx
        self.log = pd.Series()
        self.slow_log = pd.Series()
        self.current_consumption = 0
        self.max_gap = fast_interval * 2.5
        self.max_slow_gap = slow_interval * 1.1
        self.td8 = timedelta(seconds=8)
        self.td10m = timedelta(minutes=10)
        self.last_timestamp = last_timestamp
        self.consumption_last_interval = 0 # this is in W!

    def prune_log(self, time):
        self.log = self.log[time:]

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
    def __init__(self, circle_idx_by_mac, log_path, slow_log_path, cache_path, spikes_path, start_time, first_run,
                 cfg_print_data=True, slow_interval=10*60, fast_interval=10, reanalyze_intervals=None, load_cache_only=False, full_analyze=False):
        if load_cache_only and full_analyze:
            raise RuntimeError('option clash: load_cache_only and full_analyze')
        self.prevent_interval_update = load_cache_only
        self.circle_idx_by_mac = circle_idx_by_mac
        self.cache_fname = path.join(cache_path, 'energy_data.json')
        self.spikes_fname = path.join(spikes_path, 'spikes.json')
        self.slow_interval = slow_interval
        self.fast_interval = fast_interval
        self.circles = {}
        self.spike_intervals, self.spike_times = [], []
        self.cfg_print_data = cfg_print_data


        self.std = 500 # this is a rather arbitrary value ..
        self.std_intervals = 15 # this as well :P
        self.interval_length = 10 # minutes
        self.intervals_per_hour = 6
        self.interval_length_s = self.interval_length * 60
        self.interval_td = timedelta(minutes=self.interval_length)
        self.intervals_per_day = 24*60/self.interval_length
        self.intervals_start = start_time.replace(second=0,microsecond=0, minute=start_time.minute - start_time.minute % self.interval_length) + timedelta(minutes=2 * self.interval_length)
        print("intervals_start is: %s" % self.intervals_start.isoformat())
        self.day_start = self.intervals_start.replace(hour=4,minute=0)
        self.intervals_offset = int((self.intervals_start - self.day_start).total_seconds()) / self.interval_length_s
        if self.intervals_offset < 0:
            self.intervals_offset += self.intervals_per_day

        cache = self.load_cache()
        if load_cache_only and not cache:
            raise RuntimeError('no cache found during cache-only initialization')
        if not load_cache_only:
            prune = self.interval2timestamp(len(self.intervals)) - timedelta(days=2,hours=1) if cache and not full_analyze else None
            self.load_logfiles(log_path, slow_log=False, prune = prune)
            self.load_logfiles(slow_log_path, slow_log=True)
            self.clean_logs()
            last_t = self.get_last_t(start_time)
            n_intervals = max(self.timestamp2interval(last_t) + 1,0)
        self.load_spikes()
        if cache and not full_analyze:
            if not load_cache_only:
                p = ProgressBar('re-analyze last 24 hours', min(n_intervals, self.intervals_per_day))
                self.resize_intervals(n_intervals)
                if reanalyze_intervals is None:
                    reanalyze_intervals = self.intervals_per_day
                for i in range(max(len(self.intervals)-reanalyze_intervals, 0), len(self.intervals)):
                    self.update_interval(i)
                    p.next()
        else:
            if full_analyze:
                intervals_copy = self.intervals[:]
            self.intervals = [0] * n_intervals
            if full_analyze and len(self.intervals) > len(intervals_copy):
                intervals_copy += [0] * (len(self.intervals) - len(intervals_copy))
            if cfg_circle_intervals:
                for c in self.circles.values():
                    c.intervals = self.intervals[:]

            if (len(self.intervals) > 0):
                p = ProgressBar('analyzing historic consumption', len(self.intervals))
                for i in range(len(self.intervals)):
                    self.update_interval(i)
                    p.next()
            if full_analyze:
                x = [self.interval2timestamp(i) for i in range(len(self.intervals))]
                y = np.array(self.intervals) - np.array(intervals_copy)
                plt.plot(x,y)
                plt.show(block=True)
            if not first_run:
                self.save_cache()

        self.consumption_per_interval = [0] * self.intervals_per_day # Wh!
        self.consumption_per_interval_smoothed = [0] * self.intervals_per_day
        self.calc_avg_consumption_per_interval()

        self.intervals_dirty = set()
        if not load_cache_only:
            self.prune_logs(last_t)

    def prune_logs(self, now):
        prune = now - timedelta(minutes = self.interval_length * 10)
        for c in self.circles.values():
            c.prune_log(prune)

    def clean_logs(self):
        for c in self.circles.values():
            c.log.sort_index(inplace=True)
            dupl = c.log.index.duplicated(keep='first')
            if True in dupl:
                # print("log-index has %i duplicates! (for circle %i)" % (sum(dupl), c.idx))
                c.log = c.log[~dupl]

            c.slow_log.sort_index(inplace=True)
            dupl = c.slow_log.index.duplicated(keep='first')
            if True in dupl:
                # print("slow_log-index has %i duplicates! (for circle %i)" % (sum(dupl), c.idx))
                c.slow_log = c.slow_log[~dupl]

    def get_last_t(self, start_time):
        last_t = start_time
        for c in self.circles.values():
            if len(c.log) > 0:
                last_t = max(last_t, c.log.index[-1])
            if len(c.slow_log) > 0:
                last_t = max(last_t, c.slow_log.index[-1])
        return last_t

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
            if cfg_circle_intervals:
                for c in self.circles.values():
                    c.intervals += [0] * (n - len(c.intervals))

    def update_interval(self, i):
        if self.prevent_interval_update:
            raise RuntimeError('updating intervals despite update lock!')
        if i >= 0:
            t = self.intervals_start + timedelta(minutes=i*self.interval_length)
            self.intervals[i] = self.accumulated_consumption(t - self.interval_td, t, i)

    def plot_plotly_one_day(self):
        intervals_per_hour = self.intervals_per_hour
        cur_interval = len(self.intervals)-1
        part_hour = ((cur_interval-1+self.intervals_offset))%intervals_per_hour
        start = cur_interval - part_hour - self.intervals_per_day + intervals_per_hour
        entries = range(start,cur_interval+1,intervals_per_hour)
        hours = [self.interval2timestamp(i).hour for i in entries]
        x = [str(i)+":00" for i in hours]
        day_starts = [(i,self.interval2timestamp(entries[i])) for i,h in enumerate(hours) if h == 0]
        x.append(str(self.interval2timestamp(entries[-1]+intervals_per_hour).hour)+":00")
        current_consumption = [sum(self.intervals[i:i+intervals_per_hour]) for i in entries]
        avg_consumption = [sum([self.consumption_per_interval[self.cmp_interval(j)] for j in range(i,i+intervals_per_hour)]) for i in entries]
        # plt.plot(range(len(y)),y)
        # plt.pause(0.001)
        plot_plotly(current_consumption, avg_consumption, x, day_starts)

    def plot_plotly(self):
        intervals_per_hour = self.intervals_per_hour
        start = [self.interval2timestamp(i).minute for i in range(intervals_per_hour)].index(self.slow_interval / 60) # the first interval with :10 (beginning of an hour)
        entries = range(start, len(self.intervals), intervals_per_hour)
        hours = [self.interval2timestamp(i).hour for i in entries]
        x = [str(i)+":00" for i in hours]
        x.append(str(self.interval2timestamp(entries[-1]+intervals_per_hour).hour)+":00")
        day_starts = [(i,self.interval2timestamp(entries[i])) for i,h in enumerate(hours) if h == 0]
        current_consumption = [sum(self.intervals[i:i+intervals_per_hour]) for i in entries]
        avg_consumption = [sum([self.consumption_per_interval[self.cmp_interval(j)] for j in range(i,i+intervals_per_hour)]) for i in entries]
        plot_plotly(current_consumption, avg_consumption, x, day_starts)

    def plot_current_and_historic_consumption(self):
        plt.ion()
        plt.figure(3)
        plt.clf()
        intervals_per_hour = self.intervals_per_hour
        cur_interval = len(self.intervals)-1
        part_hour = ((cur_interval-1+self.intervals_offset))%intervals_per_hour
        start = cur_interval - part_hour - self.intervals_per_day + intervals_per_hour
        entries = range(start,cur_interval+1,intervals_per_hour)
        x = [str(self.interval2timestamp(i).hour)+":00" for i in entries]
        x.append(str(self.interval2timestamp(entries[-1]+intervals_per_hour).hour)+":00")
        # x = [self.interval2timestamp(i).replace(minute=0) for i in entries]
        y = [sum(self.intervals[i:i+intervals_per_hour]) for i in entries]
        plt.bar(range(len(y)),y)
        plt.xticks(range(len(x)),x)
        y = [sum([self.consumption_per_interval[self.cmp_interval(j)] for j in range(i,i+intervals_per_hour)]) for i in entries]
        plt.plot(range(len(y)),y)
        plt.pause(0.001)
        self.plot_current_and_historic_consumption1()
        # self.plot_current_day()


    def plot_current_and_historic_consumption1(self):
        plt.ion()
        plt.figure(0)
        plt.clf()
        start = max(len(self.intervals)-self.intervals_per_day, 0)
        interval_range = range(start, len(self.intervals))
        x = [self.interval2timestamp(i) for i in interval_range]

        historic_consumption = [self.consumption_per_interval[(i+self.intervals_offset)%self.intervals_per_day] for i in interval_range]
        plt.plot(x,np.cumsum(historic_consumption), label = 'historic consumption', color='blue')

        y = np.cumsum(self.intervals[-self.intervals_per_day:])
        plt.plot(x,y, label='current consumption', color='red')

        plt.legend(loc='best')
        plt.pause(0.001)

        plt.figure(2)
        plt.clf()
        plt.plot(x, historic_consumption, label='historic consumption', color='blue')
        historic_consumption = [self.consumption_per_interval_smoothed[(i+self.intervals_offset)%self.intervals_per_day] for i in interval_range]
        plt.plot(x, historic_consumption, label='historic consumption (smoothed)', color='yellow')
        plt.plot(x, self.intervals[-self.intervals_per_day:], label='current consumption', color='red')
        plt.legend(loc='best')
        plt.pause(0.001)

    def plot_current_day(self):
        plt.ion()
        plt.figure(4)
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

    def plot_data(self, idx, wait):
        plt.ioff() if wait else plt.ion()
        plt.clf()
        day_start = False
        accumulated = False
        # limit_time = True
        func = np.cumsum if accumulated else lambda x: np.array(x)
        idx = len(self.intervals)-1 if day_start else idx
        time_start = self.day_start if day_start else self.interval2timestamp(idx)
        x = [time_start + timedelta(minutes=self.interval_length * i) for i in range(self.intervals_per_day)]
        # if limit_time:
        #     x = [:]
        consumption_per_interval = self.consumption_per_interval if day_start else [self.consumption_per_interval[(idx+i+self.intervals_offset)%self.intervals_per_day] for i in range(self.intervals_per_day)]
        consumption_per_interval_smoothed = self.consumption_per_interval_smoothed if day_start else [self.consumption_per_interval_smoothed[(idx+i+self.intervals_offset)%self.intervals_per_day] for i in range(self.intervals_per_day)]
        if not accumulated:
            consumption_per_interval = [self.interval_consumption2power(i) for i in consumption_per_interval]
            consumption_per_interval_smoothed = [self.interval_consumption2power(i) for i in consumption_per_interval_smoothed]
        plt.plot(x, func(consumption_per_interval), label='historic consumption', color='blue')
        consumption_per_interval_smoothed = np.array(consumption_per_interval_smoothed)
        plt.plot(x, func(consumption_per_interval_smoothed), label='smoothed historic consumption', color='yellow')
        plt.plot(x, func(consumption_per_interval_smoothed - self.std_intervals), label='smoothed historic consumption std', color='blue')
        plt.plot(x, func(consumption_per_interval_smoothed + self.std_intervals), label='smoothed historic consumption std', color='blue')

        plt.plot(x, func(consumption_per_interval)*0.9, label='4/5th feedback', color='green')
        if accumulated:
            plt.ylim(0,2500)
            # plt.plot([x[-2]]*2,np.sum(self.consumption_per_interval)+np.array([self.std,-self.std]),'bo')
            plt.plot([x[-2]]*3,np.sum(self.consumption_per_interval)*0.9+np.array([self.std,0,-self.std]),'go')
        else:
            plt.ylim(None,300)

        i0 = self.day_start_interval(idx) if day_start else idx
        while i0 > 0:
            if i0 > 0 and idx-i0 > 0:
                plt.plot(x[:idx-i0+1], func([self.interval_consumption2power(i) for i in self.intervals[i0:i0+len(x)]]), color='red') #label='current consumption'
                # plt.plot(x[:idx-i0+1], self.intervals[i0:], label='current consumption')
            i0 -= self.intervals_per_day
        plt.legend(loc='best')
        plt.show() if wait else plt.pause(0.001)

    def accumulated_consumption(self, begin, end, i): # for an interval
        update_past_interval = i == len(self.intervals)-2
        if cfg_circle_intervals or update_past_interval:
            t = []
            for c in self.circles.values():
                cc = c.accumulated_consumption(begin, end)
                if cfg_circle_intervals:
                    c.intervals[i] = cc
                if update_past_interval:
                    c.consumption_last_interval = self.interval_consumption2power(cc) # convert to W
                t.append(cc)
            return sum(t)
        return sum(c.accumulated_consumption(begin, end) for c in self.circles.values())

    def calc_avg_consumption_per_interval(self):
        consumptions = [[] for _ in range(self.intervals_per_day)]
        for i,c in enumerate(self.intervals[:-1], start=self.intervals_offset): #skip the last interval (the one that is currently filled up)
            consumptions[i % self.intervals_per_day].append((c, i in self.spike_intervals))
        for i,c in enumerate(consumptions):
            if len(c) > 0:
                no_spikes = values = [v[0] for v in c if v[1] != True]
                if len(no_spikes) == 0: # we only have spike values in c
                    values = [v[0] for v in c] # => use them anyway
                self.consumption_per_interval[i] = sum(values) / len(values)

    def smooth_avg_consumption(self):
        self.consumption_per_interval_smoothed =  smooth(np.array(self.consumption_per_interval), 11)

    def load_spikes(self):
        try:
            spike_times = json.load(open(self.spikes_fname))
            self.spike_times = [pd.Timestamp(s) for s in spike_times]
            self.spike_intervals = [self.timestamp2interval(t) for t in self.spike_times]
        except Exception:
            return False
        print("successfully loaded spike intervals")
        return True

    def load_cache(self):
        try:
            cache = json.load(open(self.cache_fname))
            intervals = cache['intervals']
            if cfg_circle_intervals:
                circle_intervals = cache['circle_intervals'] if 'circle_intervals' in cache else None
            if pd.Timestamp(cache['intervals_start']) != self.intervals_start or cache['intervals_offset'] != self.intervals_offset:
                print("cache doesn't seem to match current config! => discarding..")
                return False
        except Exception:
            return False
        self.intervals = intervals
        if cfg_circle_intervals:
            for idx,intv in circle_intervals.items():
                self.circle(int(idx)).intervals = intv
        print("successfully loaded cache")
        return True

    def save_cache(self):
        cache = {'intervals_start': self.intervals_start.isoformat(), 'intervals_offset': self.intervals_offset}
        cache['intervals'] = self.intervals
        if cfg_circle_intervals:
            cache['circle_intervals'] = {c.idx: c.intervals for c in self.circles.values()}
        with open(self.cache_fname, 'w') as f:
            json.dump(cache, f, default=lambda o: o.__dict__)

    def save_spikes(self):
        with open(self.spikes_fname, 'w') as f:
            json.dump([t.isoformat() for t in self.spike_times], f)

    def circle(self, idx):
        if idx in self.circles:
            return self.circles[idx]
        else:
            c = CircleData(idx, self.fast_interval, self.slow_interval, self.intervals_start)
            self.circles[idx] = c
            return c

    def circle_from_mac(self, mac):
        return self.circle(self.circle_idx_by_mac[mac])

    def load_logfiles(self, path, slow_log, prune = None):
        item = 2 if slow_log else 1
        files = [ f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)) and f.endswith('.log') ]
        if prune != None:
            files = [f for f in files if (prune - dateutil.parser.parse(f[:10])).total_seconds() < 0]
        p = ProgressBar('loading logfiles' + (' (interval data)' if slow_log else ''), len(files))
        for fname in files:
            mac = fname[-10:-4]
            circle = self.circle_from_mac(mac)
            f = codecs.open(os.path.join(path,fname), encoding="latin-1")
            raw_lines = [line.strip() for line in f.readlines() if len(line) > 0]
            raw_lines = [l.split(',') for l in raw_lines] #[line.strip().split(',') for line in f.readlines()]
            num_items = 3 if slow_log else 2
            lines = [l for l in raw_lines if len(l) == num_items]
            if len(raw_lines) != len(lines):
                print("WARNING: inconsistent log: %s" % fname)
            try:
                series = pd.Series([np.max(np.float(l[item]),0) for l in lines], index=[pd.Timestamp(l[0]) for l in lines])
            except Exception as e:
                print("error loading %s" % fname)
                raise e
            if slow_log:
                circle.slow_log = circle.slow_log.append(series)
            else:
                circle.log = circle.log.append(series)
            p.next()

    def timestamp2interval(self, ts):
        return int(ceil((ts - self.intervals_start).total_seconds() / self.interval_length_s))

    def interval2timestamp(self, i):
        return self.intervals_start + timedelta(minutes = i * self.interval_length)

    def cmp_interval(self, i): #i: interval index
        return (i + self.intervals_offset) % self.intervals_per_day # returns index for self.consumption_per_interval

    def cmp_interval_ts(self, i): #i: idx for self.consumption_per_interval
        return self.day_start + timedelta(minutes=i * self.interval_length)

    def day_start_interval(self, i):
        return (i/self.intervals_per_day) * self.intervals_per_day - self.intervals_offset + \
               (self.intervals_per_day if i%self.intervals_per_day > (self.intervals_per_day - self.intervals_offset) else 0)

    def add_spike(self, time, circle_idx):
        i = self.timestamp2interval(time)
        if not i in self.spike_intervals:
            self.spike_intervals.append(i)
            self.spike_times.append(time)
            print("add spike:", circle_idx+1, time.isoformat())

    def add_value(self, mac, timestamp, value, slow_log, value_1s = None):
        c = self.circle_from_mac(mac)
        if slow_log:
            log = c.slow_log
        else:
            c.current_consumption = value
            if value_1s > 100 and value_1s - value > 15:
                c.current_consumption = value_1s
            if c.current_consumption - c.consumption_last_interval > cfg_spike_consumption:
                self.add_spike(timestamp, c.idx)
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

    def current_consumption(self): # current consumption in W
        return sum(c.current_consumption for c in self.circles.values())

    def comparison_consumption(self): # returns the (smoothed version of the) average consumption for the current time interval
        cmp_interval = self.cmp_interval(len(self.intervals)-1)
        return self.interval_consumption2power(self.consumption_per_interval_smoothed[cmp_interval]) # convert from Wh to (average) W

    def current_accumulated_consumption_24h(self):
        return sum(self.intervals[-self.intervals_per_day:])

    def comparison_avg_accumulated_consumption_24h(self, ts):
        current_interval = len(self.intervals)-1
        interval_ts = self.interval2timestamp(current_interval) # timestamp of current interval
        consumption = sum(self.consumption_per_interval)
        if ts > interval_ts: # current time is 'beyond' intervals => all intervals are filled up => use full comparison data
            return consumption
        else:
            cmp_interval = self.cmp_interval(current_interval) # this is the idx of the current *comparison* interval that is being filled up
            part_sec = (interval_ts - ts).total_seconds()
            if part_sec > self.interval_length_s: # ts is before the current interval => shouldn't happen!
                print("!!warning: ts before current interval") # TODO!
                # raise RuntimeError("ts before current interval")
            part = part_sec / self.interval_length_s
            return consumption - part * self.consumption_per_interval[cmp_interval]

    def interval_consumption2power(self, wh): #converts wh to W based on interval length
        return wh * 60 / self.interval_length

    def calculate_std(self):
        """calculates both the standard deviation of the so-far measured 24h consumptions
            and of the distance of the (interval) consumptions from the average (and smoothed) consumptions (in W!)
        """
        # from 6:00 to 1:00
        start = (6-4)*60*60 / self.interval_length_s - self.intervals_offset
        end = (25-4)*60*60 / self.interval_length_s - self.intervals_offset
        v = [] # this is for the 24h consumptions
        v2 = [] # this is for the std of the interval consumptions
        for i in range(start,end): # i: end-time of a day-interval / everything between 6:00 and 1:00
            for i1 in range(i,len(self.intervals)-1,self.intervals_per_day): # check all possible end-times (skip the very last interval)
                if i1 >= 0:
                    i0 = i1 - self.intervals_per_day # i0: start of the day-interval
                    if i0 >= 0: # within measured time?
                        v.append(sum(self.intervals[i0:i1])) # 24h consumption
                    cmp_interval = self.cmp_interval(i1)
                    d = self.interval_consumption2power(self.intervals[i1] - self.consumption_per_interval_smoothed[cmp_interval]) # in W!
                    v2.append(d*d)
        if len(v) > 5:
            self.std = np.std(v)
        if len(v2) > 5:
            self.std_intervals = sqrt(np.mean(v2))
