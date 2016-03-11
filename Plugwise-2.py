from serial.serialutil import SerialException
from plugwise.api import *
from datetime import datetime, timedelta
from EnergyData import EnergyData
import time, calendar, os, logging, json
import pandas as pd
import numpy as np

cfg_plot_data = True
cfg_print_data = True

if False:
    circle_from_mac =  {'78DB3F':1, '8FB7BB':2, '8FB86B':3, '8FD194':4,
                     '8FD25D':5, '8FD2DE':6, '8FD33A':7, '8FD358':8, '8FD472':9}

    # data@home/ # pd.Timestamp('2016-01-04T15:51:40.296000')
    # data # pd.Timestamp('2015-12-17T23:27:40.125000')
    energy_data = EnergyData(circle_from_mac, "/Users/jhammers/Dropbox/Eigene Dateien/phd/Projekte/2_Power Plant/energiedaten von thomas/data@home/",
                             "/Users/jhammers/InfoPlant", "/Users/jhammers/InfoPlant", pd.Timestamp('2016-01-04T15:51:40.296000'), False, reanalyze_intervals=10)
    energy_data.calculate_std()
    energy_data.smooth_avg_consumption()
    # energy_data.comparison_avg_accumulated_consumption_24h(pd.Timestamp('2016-01-05T15:51:40.296000'))
    # energy_data.comparison_avg_accumulated_consumption_24h(energy_data.interval2timestamp(len(energy_data.intervals)-1) - timedelta(seconds=5))
    # energy_data.current_accumulated_consumption_24h()
    # energy_data.comparison_avg_accumulated_consumption_24h(energy_data.day_start) # - timedelta(hours=20,minutes=1,seconds=5)
    # energy_data.update_day_start(get_now())
    idx = len(energy_data.intervals)-1
    if False:
        energy_data.plot_current_and_historic_consumption_2(energy_data.timestamp2interval(energy_data.day_start + timedelta(hours=8)), True)
        exit()
    i0 = idx-energy_data.intervals_per_day
    while True:
        i = idx
        while i >= i0:
            for j in range(6):
                i -= 1
                energy_data.plot_current_and_historic_consumption_2(i, False)
            energy_data.plot_current_and_historic_consumption_2(i, True)
    exit()

json.encoder.FLOAT_REPR = lambda f: ("%.2f" % f)

def get_now():
    #return datetime.now()
    return datetime.utcnow()-timedelta(seconds=time.timezone)

def get_timestamp():
    return get_now().isoformat()

class Limiter(object):
    def __init__(self, min_deviation = 0.2, min_timediff = timedelta(hours=1), first_update = timedelta(minutes=1), init_value = -999):
        self.last_update = get_now() - min_timediff + first_update
        self.value = init_value
        self.min_deviation = min_deviation
        self.min_timediff = min_timediff

    def update(self, val):
        if abs(val-self.value) > self.min_deviation and get_now() - self.last_update > self.min_timediff:
            self.value = val
            self.last_update = get_now()
            return True
        return False


cfg = json.load(open("config/pw-hostconfig.%sjson" % ('win.' if os.name=='nt' else '')))

port = cfg['serial']
port2 = cfg['serial2'] if 'serial2' in cfg else port
log_path = cfg['log_path']+'/'
slow_log_path = cfg['slow_log_path']+'/'
debug_path = cfg['debug_path']+'/'
energy_log_path = cfg['energy_log_path']+'/'
# make sure log directory exists
for path in [log_path, slow_log_path, debug_path, energy_log_path]:
    if not os.path.exists(path):
        os.makedirs(path)

open_logcomm(debug_path+"pw-communication.log")

class PWControl(object):

    def __init__(self, first_run = False, log_interval = 10, gather_historic_data = False):

        self.twig_limiter = Limiter()
        self.curfile = open(debug_path+'pwpower.log', 'w')
        self.statusfname = debug_path+'pw-status.json'
        self.statusdumpfname = debug_path+'pw-statusdump.json'
        self.session_fname = log_path+'session.json'
        self.logfiles = dict()

        sconf = json.load(open('config/pw-conf.json'))
        #set log settings
        if sconf.has_key('log_comm'):
            log_comm(sconf['log_comm'].strip().lower() == 'yes')
        if sconf.has_key('log_level'):
            if sconf['log_level'].strip().lower() == 'debug':
                log_level(logging.DEBUG)
            elif sconf['log_level'].strip().lower() == 'info':
                log_level(logging.INFO)
            elif sconf['log_level'].strip().lower() == 'error':
                log_level(logging.ERROR)
            else:
                log_level(logging.INFO)


        #read the static configuration
        self.circles = []
        self.bymac = dict()
        try:
            self.device = Stick(port, timeout=1)
        except (OSError, SerialException):
            self.device = Stick(port2, timeout=1)

        def add_circle(i, cfg = None):
            item = sconf['static'][i]
            #remove tabs which survive dialect='trimmed'
            for key in item:
                if isinstance(item[key],str): item[key] = item[key].strip()
            mac, mac2 = str(item.get('mac')), str(item.get('mac2'))
            self.bymac[mac]=i
            self.bymac[mac[-6:]]=i
            self.bymac[mac2]=i
            self.bymac[mac2[-6:]]=i
            if cfg == None:
                #exception handling timeouts done by circle object for init
                c = Circle(item['mac'], self.device, item)
                cfg = 0 if c.online else 1
                if cfg == 1:
                    c = Circle(item['mac2'], self.device, item)
            elif cfg == 0:
                c = Circle(item['mac'], self.device, item)
            else:
                c = Circle(item['mac2'], self.device, item)
            self.circles.append(c)
            # self.set_interval_production(self.circles[-1])
            c.force_interval(log_interval)
            info("adding circle: %s" % (c.attr['name'],))
            c.written_offline = 0
            if (c.online):
                print("successfully added circle %s" % (c.short_mac(),))
            else:
                print("!! failed to add circle %s" % (c.short_mac(),))
            return cfg

        cfg = add_circle(0)
        if not self.circles[0].online:
            raise RuntimeError("Could not connect to circle+")
        for i in range(1,len(sconf['static'])):
            add_circle(i, cfg)
        self.cfg = cfg

        if gather_historic_data:
            print("gathering full historic data...")
            self.gather_historic_data = True
            for circle in self.circles:
                print(circle.mac)
                circle.first_run = False
                self.log_recording(circle)
            print("done")
            raise RuntimeError("gathered historic data :P")
        else:
            self.gather_historic_data = False

        try:
            session = json.load(open(self.session_fname))
            last_logs = session['last_logs']
        except Exception:
            first_run = True

        self.first_run = first_run
        if first_run:
            if False in [c.online for c in self.circles]:
                raise RuntimeError("all circles must be sucessfully added on first run")
            for c in self.circles:
                c.first_run = True
                c.set_log_interval(log_interval)
                c.last_log = c.get_info()['last_logaddr']
                # c.last_log_ts = ??
            self.session_start = get_now()
            # self.write_session()
            self.last_logs = []
        else:
            self.session_start = pd.Timestamp(session['start']).to_datetime()
            cfg_change = self.cfg != session['cfg']
            if cfg_change and False in [c.online for c in self.circles]:
                raise RuntimeError("all circles must be sucessfully added on config change")
            last_log_macs = [l['mac'] for l in last_logs]
            for c in self.circles:
                if c.mac in last_log_macs:
                    li = last_log_macs.index(c.mac)
                    ll = last_logs[li]
                    if cfg_change:
                        c.first_run = True
                        c.last_log = c.get_info()['last_logaddr']
                    else:
                        c.first_run = False
                        c.last_log = ll['last_log']
                        c.last_log_idx = ll['last_log_idx']
                        c.last_log_ts = ll['last_log_ts']
                        c.cum_energy = ll['cum_energy']
                    del last_log_macs[li]
                    del last_logs[li]
                else:
                    if not cfg_change:
                        print('!! circle (mac: %s) not found in last_logs' % c.mac)
                    error('circle (mac: %s) not found in last_logs' % c.mac)
                    c.first_run = True
                    c.last_log = c.get_info()['last_logaddr']
                    continue
            self.last_logs = last_logs

        self.setup_logfiles()

    def write_session(self):
        lastlogs = [{'mac':c.mac,'last_log':c.last_log,'last_log_idx':c.last_log_idx,'last_log_ts':c.last_log_ts,'cum_energy':c.cum_energy} for c in self.circles]
        lastlogs += self.last_logs
        data = {'start': self.session_start.isoformat(), 'cfg': self.cfg, 'last_logs': lastlogs}
        with open(self.session_fname, 'w') as f:
            json.dump(data, f, default=lambda o: o.__dict__)
        
    def log_status(self):
        try:
            circles = [c.get_status() for c in self.circles]
            with open(self.statusfname, 'w') as f:
                json.dump(circles, f, default=lambda o: o.__dict__)
        except Exception as reason:
            error("Error in dump_status: %s" % (reason,))

    def dump_status(self):
        try:
            circles = [c.dump_status() for c in self.circles]
            with open(self.statusdumpfname, 'w') as f:
                json.dump(circles, f, default=lambda o: o.__dict__)
        except Exception as reason:
            error("Error in dump_status: %s" % (reason,))

    def sync_time(self):
        for c in self.circles:
            if not c.online:
                continue
            try:
                info("sync_time: circle %s time is %s" % (c.attr['name'], c.get_clock().isoformat()))
                if c.type()=='circle+':
                    c.set_circleplus_datetime(get_now())
                c.set_clock(get_now())
            except (ValueError, TimeoutException, SerialException) as reason:
                error("Error in sync_time: %s" % (reason,))

    def set_interval_production(self, c):
        if not c.online:
            return
        try:
            prod = c.attr['production'].strip().lower() in ['true', '1', 't', 'y', 'yes', 'on']
            interv = int(c.attr['loginterval'].strip())
            if (c.interval != interv) or (c.production != prod):
                c.set_log_interval(interv, prod)
        except (ValueError, TimeoutException, SerialException) as reason:
            error("Error in set_interval_production: %s" % (reason,))


    def setup_logfiles(self):
        #close all open log files
        for m, f in self.logfiles.iteritems():
            f.close()
        #open logfiles
        self.logfiles = dict()
        today = get_now().date().isoformat()
        for c in self.circles:
            self.logfiles[c.mac] = open(log_path + today + '_' + c.mac + '.log', 'a')

    def ten_seconds(self): # get/write current power-usage
        """
        Failure to read an actual usage is not treated as a severe error.
        The missed values are just not logged. The circle ends up in 
        online = False, and the self.test_offline() tries to recover
        """

        self.curfile.seek(0)
        self.curfile.truncate(0)
        for mac, f in self.logfiles.iteritems():
            try:
                c = self.circles[self.bymac[mac]]                
            except:
                print("!! logfile-mac not found in circles: %s" % mac)
                error("Error in ten_seconds(): mac from controls not found in circles")
                continue
            ts = get_now()
            ts_str = ts.isoformat()
            def write_offline():
                if c.written_offline < 10:
                    # f.write("%s, offline\n" % (ts,))
                    self.curfile.write("%s, offline\n" % (mac,))
                    c.written_offline += 1
                energy_data.report_offline(c.mac, ts_str)
            if not c.online:
                # write_offline()
                # print("should not happen!")
                continue

            #prepare for logging values
            try:
                usage_1s, usage, _, _ = c.get_power_usage()
                if usage < 0 and not c.production:
                    usage = 0
                if usage_1s < 0 and not c.production:
                    usage_1s = 0
                c.written_offline = 0
                if energy_data.add_value(mac, ts, usage, slow_log=False, value_1s = usage_1s):
                    f.write("%s, %8.2f\n" % (ts_str, usage,))
                    self.curfile.write("%s, %.2f\n" % (mac, usage))
            except ValueError:
                print("should not happen! (ValueError in get_power_usage())")
                f.write("%5d, \n" % (ts,))
                self.curfile.write("%s, \n" % (mac,))
            except (TimeoutException, SerialException) as reason:
                #for continuous monitoring just retry
                error("Error in ten_seconds(): %s" % (reason,))
                assert(c.online == False)
                write_offline()

            f.flush()
        self.curfile.flush()
        return

    def log_recording(self, circle):
        """
        Failure to read recordings for a circle will prevent writing any new
        history data to the log files. Also the counter in the counter file is not
        updated. Consequently, at the next call (one hour later) reading the  
        history is retried.
        """
        c = circle
        mac = c.mac
        if not c.online:
            return False

        #figure out what already has been logged.
        try:
            c_info = c.get_info()
            #update c.power fields for administrative purposes
            c.get_power_usage()
        except ValueError:
            return False
        except (TimeoutException, SerialException) as reason:
            error("Error in log_recording() get_info: %s" % (reason,))
            return False

        last = c_info['last_logaddr']
        first = c.last_log
        idx = c.last_log_idx
        if c.last_log_ts != 0:
            last_dt = datetime.utcfromtimestamp(c.last_log_ts)-timedelta(seconds=time.timezone)
        else:
            last_dt = None

        if last_dt ==None:
            debug("start with first %d, last %d, idx %d, last_dt None" % (first, last, idx))
        else:
            debug("start with first %d, last %d, idx %d, last_dt %s" % (first, last, idx, last_dt.strftime("%Y-%m-%d %H:%M")))
        #check for buffer wrap around
        #The last log_idx is 6015. 6016 is for the range function
        if last < first:
            if (first == 6015 and idx == 4) or first >= 6016:
                first = 0
            else:
                #last = 6016
                #TODO: correct if needed
                last = 6015
        log = []
        try:
            #read one more than request to determine interval of first measurement
            #TODO: fix after reading debug log
            if last_dt == None:
                if first>0:
                    powlist = c.get_power_usage_history(first-1)
                    last_dt = powlist[3][0]
                    #The unexpected case where both consumption and production are logged
                    #Probably this case does not work at all
                    if powlist[1][0]==powlist[2][0]:
                       #not correct for out of sync usage and production buffer
                       #the returned value will be production only
                       last_dt=powlist[2][0]
                    debug("determine last_dt - buffer dts: %s %s %s %s" %
                        (powlist[0][0].strftime("%Y-%m-%d %H:%M"),
                        powlist[1][0].strftime("%Y-%m-%d %H:%M"),
                        powlist[2][0].strftime("%Y-%m-%d %H:%M"),
                        powlist[3][0].strftime("%Y-%m-%d %H:%M")))
                elif first == 0:
                    powlist = c.get_power_usage_history(0)
                    if len(powlist) > 2 and powlist[0][0] is not None and powlist[1][0] is not None:
                        last_dt = powlist[0][0]
                        #subtract the interval between index 0 and 1
                        last_dt -= powlist[1][0] - powlist[0][0]
                    else:
                        #last_dt cannot be determined yet. wait for 2 hours of recordings. return.
                        info("log_recording: last_dt cannot be determined. circles did not record data yet.")
                        return False

            #loop over log addresses and write to file
            for log_idx in range(first, last+1):
                buffer = c.get_power_usage_history(log_idx, last_dt)
                idx = idx % 4
                debug("len buffer: %d, production: %s" % (len(buffer), c.production))
                for i, (dt, watt, watt_hour) in enumerate(buffer):
                    if i >= idx and not dt is None and dt >= last_dt:
                        #if the timestamp is identical to the previous, add production to usage
                        #in case of hourly production logging, and end of daylightsaving, duplicate
                        #timestamps can be present for two subsequent hours. Test the index
                        #to be odd handles this.
                        idx = i + 1
                        if dt == last_dt:
                            error('dt == last_dt')

                        log.append([dt, watt, watt_hour])
                        info("circle buffers: %s %d %s %d %d" % (mac, log_idx, dt.strftime("%Y-%m-%d %H:%M"), watt, watt_hour))
                        debug("proce with first %d, last %d, idx %d, last_dt %s" % (first, last, idx, last_dt.strftime("%Y-%m-%d %H:%M")))
                        last_dt = dt

            # if idx < 4:
                # #not completely read yet.
                # last -= 1
            # if idx >= 4:
                # #not completely read yet.
                # last += 1
            #idx = idx % 4
        except ValueError:
            error("Error: Failed to read power usage")
            return False
        except (TimeoutException, SerialException) as reason:
            #TODO: Decide on retry policy
            #do nothing means that it is retried after one hour (next call to this function).
            error("Error in log_recording() wile reading history buffers - %s" % (reason,))
            return False

        debug("end   with first %d, last %d, idx %d, last_dt %s" % (first, last, idx, last_dt.strftime("%Y-%m-%d %H:%M")))

        #update last_log outside try block.
        #this results in a retry at the next call to log_recording
        c.last_log = last
        c.last_log_idx = idx
        c.last_log_ts = calendar.timegm((last_dt+timedelta(seconds=time.timezone)).utctimetuple())

        #initialisation to a value in the past.
        #Value assumes 6016 logadresses = 6016*4 60 minutes logs = 1002.n days
        #just set this several years back. Circles may have been unplugged for a while
        f = None
        for dt, watt, watt_hour in log:
            if dt is not None and not c.first_run:
                #calculate cumulative energy in Wh
                c.cum_energy = c.cum_energy + watt_hour
                ts_str = dt.isoformat()
                if not self.gather_historic_data:
                    energy_data.add_value(mac, dt, watt_hour, slow_log=True)
                watt = "%15.4f" % (watt,)
                watt_hour = "%15.4f" % (watt_hour,)

                today = dt.date().isoformat()
                fname = slow_log_path + today + '_' + mac + '.log'
                f = open(fname, 'a')

                f.write("%s, %s, %s\n" % (ts_str, watt, watt_hour))

        if not f == None:
            f.close()

        info("circle buffers: %s %s read from %d to %d" % (mac, c.attr['name'], first, last))

        # store lastlog addresses to session file
        if not self.gather_historic_data:
            self.write_session()
        return True

    def log_recordings(self):
        debug("log_recordings")
        for circle in self.circles:
            ret = self.log_recording(circle)
            if circle.first_run and not ret:
                raise RuntimeError("failed to get recordings on first run for circle: %s" % circle.mac)
            circle.first_run = False
        self.first_run = False

    def test_offline(self):
        """
        When an unrecoverable communication failure with a circle occurs, the circle
        is set online = False. This function will test on this condition and if offline,
        it test whether it is available again, and if so, it will recover
        control settings and switching schedule if needed.
        In case the circle was offline during initialization, a reinit is performed.
        """
        for c in self.circles:
            if not c.online:
                try:
                    c.ping()
                    if c.online:
                        #back online. Make sure switch and schedule is ok
                        if not c.initialized:
                            c.reinit()
                            self.set_interval_production(c)
                except ValueError:
                    continue
                except (TimeoutException, SerialException) as reason:
                    debug("Error in test_offline(): %s" % (reason,))
                    continue

        
    def connect_unknown_nodes(self):
        for newnodemac in self.device.unjoined:
            newnode = None
            try:
                newnode = self.circles[self.bymac[newnodemac]]
            except:
                info("connect_unknown_node: not joining node with MAC %s: not in configuration" % (newnodemac,))        
            #accept or reject join based on occurence in pw-conf.json
            self.device.join_node(newnodemac, newnode != None)
        #clear the list
        self.device.unjoined.clear()
        #a later call to self.test_offline will initialize the new circle(s)
        #self.test_offline()
        
    def run(self):

        now = get_now()
        day = now.day
        hour = now.hour
        minute = now.minute

        self.sync_time()
        self.dump_status()
        self.log_recordings()

        energy_data.update_intervals()
        energy_data.calc_avg_consumption_per_interval()
        energy_data.smooth_avg_consumption()
        energy_data.calculate_std()
        energy_data.update_start_interval()
        energy_data.save_cache()
        energy_data.plot_current_and_historic_consumption()
        start_interval_updated = True

        ## TODO: read all previous data to logging-facility (probably earlier!)

        offline = []
      
        #Inform network that nodes are allowed to join the network
        #Nodes may start advertising themselves with a 0006 message.
        try:
            self.device.enable_joining(True)
        except:
            error("PWControl.run(): Communication error in enable_joining")

        print("starting logging")
        while 1:
            #this call can take over ten seconds!
            self.test_offline()

            ##align with the next ten seconds.
            #time.sleep(10-datetime.now().second%10)

            #align to next 10 second boundary, while checking for input commands.
            # ref = datetime.now()
            # proceed_at = ref + timedelta(seconds=(10 - ref.second%10), microseconds= -ref.microsecond)
            # while datetime.now() < proceed_at:
            #     time.sleep(0.5)

            #prepare for logging values
            prev_day = day
            prev_hour = hour
            prev_minute = minute
            
            now = get_now()
            
            day = now.day
            hour = now.hour
            minute = now.minute
                
            if minute != prev_minute:
                self.log_recordings()
                self.connect_unknown_nodes() #add configured unjoined nodes every minute (although call is issued every hour..)

            if day != prev_day:
                self.setup_logfiles()
            self.ten_seconds()
            energy_data.update_intervals()
            if not start_interval_updated:
                start_interval_updated = energy_data.update_start_interval()

            diff = energy_data.current_accumulated_consumption_24h() - energy_data.comparison_avg_accumulated_consumption_24h(now)
            twig = np.clip(diff, -energy_data.std, energy_data.std) / energy_data.std
            if self.twig_limiter.update(twig):
                print("!!TWIG UPDATE!!", twig)

            current_consumption = energy_data.current_consumption()
            comparison_consumption = energy_data.comparison_consumption()
            diff = current_consumption - comparison_consumption
            if diff > 0:
                leds = min(diff / energy_data.std_intervals, 1)
            else:
                lower = max(comparison_consumption - max(energy_data.std_intervals,1), 0)
                leds = max(diff / (comparison_consumption - lower), -1)

            if cfg_print_data:
                print("cur: %.2f/%.2f %.2f/%.2f %.2f/%.2f %s" % (twig, leds, energy_data.current_consumption(), energy_data.comparison_consumption(),
                      energy_data.current_accumulated_consumption_24h(), energy_data.comparison_avg_accumulated_consumption_24h(now), now.isoformat()))

            if minute != prev_minute:
                energy_data.calc_avg_consumption_per_interval()
                energy_data.smooth_avg_consumption()
                energy_data.calculate_std()
                if cfg_plot_data:
                    energy_data.plot_current_and_historic_consumption()

            new_offline = [c.short_mac() for c in self.circles if not c.online]
            if len(offline) > 0 and len(new_offline) == 0:
                print("all circles are back online")
            elif offline != new_offline:
                print("!!the following circles are offline: %s" % (new_offline,))
            offline = new_offline

            self.log_status()
            if hour != prev_hour:
                energy_data.save_cache()
                start_interval_updated = energy_data.update_start_interval()
                if hour == 4:
                    self.sync_time()
                    info("Daily 4 AM: time synced circles.")
                #Allow resetted or unknown nodes to join the network every hour
                #NOTE: Not fully tested.
                try:
                    self.device.enable_joining(True)
                except:
                    error("PWControl.run(): Communication error in enable_joining")
                self.dump_status()


init_logger(debug_path+"pw-logger.log", "pw-logger")

try:
    # print(get_timestamp())
    # energy_data = EnergyData(log_path, slow_log_path, energy_log_path, pd.Timestamp('2016-01-04T15:51:40')) # only temporary!
    main=PWControl(gather_historic_data=False)
    if not main.gather_historic_data:
        energy_data = EnergyData(main.bymac, log_path, slow_log_path, energy_log_path, main.session_start, main.first_run)
        # energy_data.update_day_start(get_now())
        energy_data.plot_current_and_historic_consumption()
        main.run()
except:
    close_logcomm()
    raise
