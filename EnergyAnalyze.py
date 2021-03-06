print("load dependencies")
import sys
if sys.version_info.major == 2:
    sys.path.insert(1,'/Library/Python/2.7/site-packages')

import json, numpy as np, pandas as pd
from os import path

from EnergyData import EnergyData, init_matplotlib
from plotly_plot import init_plotly

timedelta = pd.offsets.timedelta
timestamp = pd.Timestamp

base_path = '/Users/jhammers/Dropbox/Eigene Dateien/phd/Projekte/1_InfoPlant Evaluation/data'

circle_from_mac = {'78DB3F': 0, '8FB7BB': 1, '8FB86B': 2, '8FD194': 3,
                   '8FD25D': 4, '8FD2DE': 5, '8FD33A': 6, '8FD358': 7, '8FD472': 8,
                   "78EE23": 0, "8FB663": 1, "8FBB68": 2, "8FB6CB": 3, "8FD32C": 4,
                   "8FD34A": 5, "8FD4F9": 6, "8FD319": 7, "8FD1A6": 8}

# json.encoder.FLOAT_REPR = lambda f: ("%.2f" % f)

class Data:
    def __init__(self, folder, restarts = None, plant_first = False):
        self.folder = folder
        self.path = path.join(base_path, folder, "3")
        self._restarts = restarts
        self.plant_first = plant_first

    def load(self):
        self.session = json.load(open(path.join(self.path, 'datalog', 'session.json')))
        start_time = pd.Timestamp(self.session['start'])
        self.data = data = EnergyData(circle_from_mac, path.join(self.path, 'datalog'), path.join(self.path, 'slow_log'),
                               self.path, self.path, start_time, False, load_cache_only=True, full_analyze=False)
        self.ts = [data.interval2timestamp(i) for i in range(len(data.intervals))] # timestamps of intervals
        self.ts_begin = data.interval2timestamp(0)
        self.ts_end = data.interval2timestamp(len(data.intervals))
        if self._restarts != None and type(self._restarts[0]) is str:
            self.restarts = [timestamp(r) for r in self._restarts]
        elif 'restarts' in self.session:
            self.restarts = [pd.Timestamp(ts) for ts in self.session['restarts']]
            if self._restarts == None:
                print("restarts")
                print(self.restarts)
                print([data.timestamp2interval(r) for r in self.restarts])
                print([i for i in range(len(self.restarts))])
            else:
                self.restarts = [self.restarts[r] for r in self._restarts]
        if 'restarts' in self.__dict__ and len(self.restarts) == 2:
            self.consumption = [0] * 3
            self.consumption[0] = self.print_week_info("week 1", data.interval2timestamp(0), self.restarts[0])
            self.consumption[1] = self.print_week_info("week 2", self.restarts[0], self.restarts[1], self.plant_first)
            self.consumption[2] = self.print_week_info("week 3", self.restarts[1], data.interval2timestamp(len(data.intervals)), not self.plant_first)
            # if not self.plant_first:
            #     self.consumption[1], self.consumption[2] = self.consumption[2], self.consumption[1]
        else:
            print("TODO: restarts (%s)" % self.folder)
        # self.daily_consumption

    def print_week_info(self, name, start, end, plant = False, cached=True):
        consumption = self.avg_W_per_timeframe(start, end, cached)
        print("%s: %.3fW (%s)%s" % (name, consumption,
                                    str(end - start), " [PLANT]" if plant else ""))
        return consumption

    def avg_W_per_timeframe(self, start, end, cached=True):
        if not cached:
            raise RuntimeError('"not cached" not implemented')
        data = self.data
        i_start, i_end = data.timestamp2interval(start), data.timestamp2interval(end)-2
        return data.interval_consumption2power(np.mean([data.intervals[i] for i in range(i_start, i_end)]))

    def plot_daily(self):
        x = [t for t in self.ts if t.hour == 0 and t.minute == self.data.interval_length]
        xd = x[:]  # [i - timedelta(hours=12) for i in x] # actual x-axis data for bar plot (first day)
        y = [self.avg_W_per_timeframe(self.ts_begin, x[0])] #first day
        xd.insert(0, x[0] - timedelta(hours=24))
        for i in range(len(x)-1): #all full days
            y.append(self.avg_W_per_timeframe(x[i], x[i+1]))
        y.append(self.avg_W_per_timeframe(x[-1], self.ts_end)) #last day
        r = self.restarts
        b = [next(i for i,t in enumerate(xd[:-1]) if r[j] > t and r[j] < xd[i+1]) for j in range(2)]
        #plt.bar(np.arange(len(y)), y)
        plt.bar(xd, y)
        # plt.xticks(np.arange(len(x)) + 1, [i.strftime('%a, %d.%m') for i in x])

    def plot_all(self, name):
        data, ts = self.data, self.ts
        x,y = ts, data.intervals
        plt.plot(x,y)
        def date(ts, **kwargs):
            plt.annotate(ts.strftime('%a, %d.%m (%H:%M)'), xy=(ts, 0.9), xycoords=('data', 'axes fraction'), **kwargs)
        for r in [self.ts_begin] + self.restarts:
            plt.axvline(r)
            date(r)
        date(self.ts_end, horizontalalignment='right')

        # restart_intervals = [i for i in set([data.timestamp2interval(ts) for ts in self.restarts]) if i > 0]
        # plt.vlines(restart_intervals, 0, 250)
        # dates = [0] + restart_intervals + [len(data.intervals)]
        # x = range(len(data.intervals))[::300]
        # plt.xticks(x, x)
        plt.title('plot_all: %s' % name)


    # def plot_cmp(self):
    #     energy_data = self.data
    #     energy_data.calc_avg_consumption_per_interval()
    #     energy_data.calculate_std()
    #     print(energy_data.std, energy_data.std_intervals)
    #     energy_data.smooth_avg_consumption()
    #     idx = len(energy_data.intervals)-1
    #     i0 = idx-energy_data.intervals_per_day
    #     while True:
    #         i = idx
    #         while i >= i0:
    #             for j in range(6):
    #                 i -= 1
    #                 energy_data.plot_data(i, False)
    #             energy_data.plot_data(i, True)

data = [
    Data('1 (Sabrina)', ['2016-03-24T15:00', '2016-03-31T15:30']),
    Data('2 (Tina)', [3,4], False),
    Data('3 (Philip)', [1,3], True),
    Data('4 (Sarah)', [2,3], False),
    Data('5 (Anne)', [3,5], True),
    Data('6 (Rieke)', [1,2], True),
    Data('7 (Fibita)', [13,14], False)
]

plt = init_matplotlib()
for d in data:
    print(d.folder)
    d.load()
    d.plot_all(d.folder)
    # d.plot_daily()
    plt.show()


for d in data:
    if not d.plant_first:
       plt.plot(range(3), np.array(d.consumption)/d.consumption[0], label=d.folder)
plt.xticks(range(3), ["baseline", "PLANT", "graphical"])
plt.legend(loc='best')
plt.show()


# d = data[0]
# d.load()
# d.plot_all()
# plt.show()


# load_data('/Users/jhammers/Dropbox/Eigene Dateien/phd/Projekte/1_InfoPlant Evaluation/data/2 (Tina)/3')

# init_plotly()
# data.plot_plotly()

# plot_cmp()

# plot_daily()
