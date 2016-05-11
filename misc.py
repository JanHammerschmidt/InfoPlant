from sys import stdout
from datetime import datetime, timedelta
import pandas as pd

class TouchLimiter(object):
    def __init__(self, callback, max_touches = 2, timeframe = timedelta(seconds=30)):
        self.callback = callback
        self.max_touches = max_touches
        self.timeframe = timeframe
        self.touches = pd.Series()

    def touch(self, now):
        self.touches[now] = True
        if len(self.touches[now-self.timeframe:]) <= self.max_touches:
            self.callback()


class ProgressBar(object): # todo: with design
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
        stdout.write("%s [%-20s] %d%%" % (self.name, '='*int(self.c*20/self.nitems), self.c * 100/self.nitems))
        stdout.flush()

    def next(self):
        self.c += 1
        self.out()
        if self.c >= self.nitems:
            stdout.write('\n')


class TimeTrigger(object):
    def __init__(self, t, trigger, name = None): # t: "now"-timestamp, trigger: string of time (e.g. "10:00")
        from dateutil.parser import parse
        next = parse(trigger) # next trigger

        if (next - t).total_seconds() <= 0: # already was this day ..
            next = next + timedelta(days=1)
        assert((next-t).total_seconds() > 0)

        self.next_t = next
        self.name = name

    def test(self, t): # t: "now"-timestamp
        if (t - self.next_t).total_seconds() > 0: # trigger!
            self.next_t = self.next_t + timedelta(days=1) # next trigger is tomorrow
            return True
        return False

    def remaining_time(self, t): # returns remaining seconds
        return (self.next_t - t).total_seconds()

def linear_interp(v1,v2,v):
    return v1 * (1-v) + v2 * v

def linear_interp_color(c1, c2, v):
    return tuple([linear_interp(k,k2,v) for k,k2 in zip(c1,c2)])
