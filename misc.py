from sys import stdout


class ProgressBar(object):
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
        if self.c >= self.nitems:
            stdout.write('\n')


def linear_interp(v1,v2,v):
    return v1 * (1-v) + v2 * v

def linear_interp_color(c1, c2, v):
    return tuple([linear_interp(k,k2,v) for k,k2 in zip(c1,c2)])
