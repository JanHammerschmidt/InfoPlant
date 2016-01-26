from serial.serialutil import SerialException

from plugwise.api import *

from datetime import datetime, timedelta
import time, calendar, os, logging

import json

json.encoder.FLOAT_REPR = lambda f: ("%.2f" % f)

def get_now():
    #return datetime.now()
    return datetime.utcnow()-timedelta(seconds=time.timezone)

def get_timestamp():
    if epochf:
        return str(calendar.timegm(datetime.utcnow().utctimetuple()))
    else:
        return get_now().isoformat()
        # t = datetime.time(datetime.utcnow()-timedelta(seconds=time.timezone))
        # return 3600*t.hour+60*t.minute+t.second


def jsondefault(o):
    return o.__dict__

log_comm(False)

cfg = json.load(open("config/pw-hostconfig.json"))

tmppath = cfg['tmp_path']+'/'
datapath = cfg['data_path']+'/'
logpath = cfg['log_path']+'/'
# make sure log directory exists
for path in [tmppath,datapath,logpath]:
    if not os.path.exists(path):
        os.makedirs(path)

port = cfg['serial']
epochf = cfg.has_key('log_format') and cfg['log_format'] == 'epoch'

open_logcomm(logpath+"pw-communication.log")

class PWControl(object):

    def __init__(self):

        self.device = Stick(port, timeout=1)
        self.staticconfig_fn = 'config/pw-conf.json'
        self.control_fn = 'config/pw-control.json'

        self.last_control_ts = None

        self.circles = []
        self.controls = []
        self.controlsjson = dict()
        self.save_controls = False
        
        self.bymac = dict()
        self.byname = dict()

        self.curfile = open(tmppath+'pwpower.log', 'w')
        self.statusfile = open(tmppath+'pw-status.json', 'w')
        self.statusdumpfname = tmppath+'pw-statusdump.json'
        self.actfiles = dict()
        self.logfnames = dict()
        self.daylogfnames = dict()
        self.lastlogfname = datapath+'pwlastlog.log'

        #read the static configuration
        sconf = json.load(open(self.staticconfig_fn))
        for i,item in enumerate(sconf['static']):
            #remove tabs which survive dialect='trimmed'
            for key in item:
                if isinstance(item[key],str): item[key] = item[key].strip()
            self.bymac[item.get('mac')]=i
            self.byname[item.get('name')]=i
            #exception handling timeouts done by circle object for init
            self.circles.append(Circle(item['mac'], self.device, item))
            self.set_interval_production(self.circles[-1])
            info("adding circle: %s" % (self.circles[-1].attr['name'],))
            self.circles[-1].written_offline = 0
            if (self.circles[-1].online):
                print("successfully added circle %s" % (self.circles[-1].short_mac(),))
            else:
                print("!! failed to add circle %s" % (self.circles[-1].short_mac(),))

        #self.try_connect_missing_nodes()
        #return
        
        #retrieve last log addresses from persistent storage
        with open(self.lastlogfname, 'a+') as f:
            f.seek(0)
            for line in f:
                parts = line.split(',')
                mac, logaddr = parts[0:2]
                idx = 0
                ts = 0
                cum_energy = 0
                if len(parts) == 5:
                    cum_energy = float(parts[4])
                if len(parts) >= 4:
                    idx = int(parts[2])
                    ts = int(parts[3])
                logaddr =  int(logaddr)
                debug("mac -%s- logaddr -%s- logaddr_idx -%s- logaddr_ts -%s- cum_energy -%s-" % (mac, logaddr, idx, ts, cum_energy))
                try:
                    self.circles[self.bymac[mac]].last_log = logaddr
                    self.circles[self.bymac[mac]].last_log_idx = idx
                    self.circles[self.bymac[mac]].last_log_ts = ts
                    self.circles[self.bymac[mac]].cum_energy = cum_energy
                except:
                    error("PWControl.__init__(): lastlog mac not found in circles")
         
        self.poll_configuration()

    def try_connect_missing_nodes(self):
        for c in self.circles:
            if not c.online:
                self.connect_node_by_mac(c.mac)
        print("still offline:")
        print([c.short_mac() for c in self.circles if not c.online])

    def get_status_json(self, mac):
        try:
            c = self.circles[self.bymac[mac]]
            control = self.controls[self.controlsbymac[mac]]
        except:
            info("get_status_json: mac not found in circles or controls")
            return ""
        try:
            status = c.get_status()
            status["monitor"] = (control['monitor'].lower() == 'yes')
            status["savelog"] = (control['savelog'].lower() == 'yes')
            msg = json.dumps(status)
        except (ValueError, TimeoutException, SerialException) as reason:
            error("Error in get_status_json: %s" % (reason,))
            msg = ""
        return str(msg)
        
    def log_status(self):
        self.statusfile.seek(0)
        self.statusfile.truncate(0)
        self.statusfile.write('{"circles": [\n')
        comma = False
        for c in self.circles:
            if comma:
                self.statusfile.write(",\n")
            else:
                comma = True
            self.statusfile.write(self.get_status_json(c.mac))
        self.statusfile.write('\n] }\n')
        self.statusfile.flush()
        
    def dump_status(self):
        self.statusdumpfile = open(self.statusdumpfname, 'w+')
        self.statusdumpfile.write('{"circles": [\n')
        comma = False
        for c in self.circles:
            if comma:
                self.statusdumpfile.write(",\n")
            else:
                comma = True
            json.dump(c.dump_status(), self.statusdumpfile, default = jsondefault)
        self.statusdumpfile.write('\n] }\n')
        self.statusdumpfile.close()
    
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

    def read_apply_controls(self):
        debug("read_apply_controls")
        #read the user control settings
        controls = json.load(open('config/pw-control.json'))
        self.controlsjson = controls
        self.controlsbymac = dict()
        newcontrols = []        

        for i,item in enumerate(controls['dynamic']):
            #remove tabs which survive dialect='trimmed'
            for key in item:
                if isinstance(item[key],str): item[key] = item[key].strip()
            newcontrols.append(item)
            self.controlsbymac[item['mac']]=i

        #set log settings
        if controls.has_key('log_comm'):
            log_comm(controls['log_comm'].strip().lower() == 'yes')
        if controls.has_key('log_level'):
            if controls['log_level'].strip().lower() == 'debug':
                log_level(logging.DEBUG)
            elif controls['log_level'].strip().lower() == 'info':
                log_level(logging.INFO)
            elif controls['log_level'].strip().lower() == 'error':
                log_level(logging.ERROR)
            else:
                log_level(logging.INFO)

        def_item = {'switch_state':'on','name':'circle','savelog':'no','monitor':'yes'}
        for circle in self.circles:
            if not circle.mac in self.controlsbymac:
                def_item['mac'] = circle.mac
                self.controlsbymac[circle.mac] = len(newcontrols)
                newcontrols.append(def_item.copy())

        self.controls =  newcontrols

    def setup_actfiles(self):
        
        #close all open act files
        for m, f in self.actfiles.iteritems():
            f.close()
        #open actfiles according to (new) config
        self.actfiles = dict()
        now = get_now()
        today = now.date().isoformat()
        for mac, idx in self.controlsbymac.iteritems():
            if self.controls[idx]['monitor'].lower() == 'yes':
                fname = datapath + today + '_' + mac + '.log'
                f = open(fname, 'a')
                self.actfiles[mac]=f

    def test_mtime(self, before, after):
        modified = []
        if after:
            for (bf,bmod) in before.items():
                if (after.has_key(bf) and after[bf] > bmod):
                    modified.append(bf)
        return modified
     
    def poll_configuration(self):
        debug("poll_configuration()")

        if self.last_control_ts != os.stat(self.control_fn).st_mtime:
            self.last_control_ts = os.stat(self.control_fn).st_mtime
            self.read_apply_controls()
            self.setup_actfiles()
            #self.setup_logfiles()            
        #failure to apply control settings to a certain circle results
        #in offline state for that circle, so it get repaired when the
        #self.test_offline() method detects it is back online
        #a failure to load a schedule data also results in online = False,
        #and recovery is done by the same functions.


    def write_control_file(self):
        #write control file for testing purposes
        fjson = open("config/pw-control.json", 'w')
        self.controlsjson['dynamic'] = self.controls
        json.dump(self.controlsjson, fjson)
        fjson.close()
     
    def ten_seconds(self):
        """
        Failure to read an actual usage is not treated as a severe error.
        The missed values are just not logged. The circle ends up in 
        online = False, and the self.test_offline() tries to recover
        """

        self.curfile.seek(0)
        self.curfile.truncate(0)
        for mac, f in self.actfiles.iteritems():
            try:
                c = self.circles[self.bymac[mac]]                
            except:
                error("Error in ten_seconds(): mac from controls not found in circles")
                continue
            ts = get_timestamp()
            def write_offline():
                if c.written_offline < 10:
                    f.write("%s, offline\n" % (ts,))
                    self.curfile.write("%s, offline\n" % (mac,))
                    c.written_offline += 1
                    return True
                return False
            if not c.online:
                write_offline()
                # print("should not happen!")
                continue

            #prepare for logging values
            try:
                _, usage, _, _ = c.get_power_usage()
                #print("%10d, %8.2f" % (ts, usage,))
                c.written_offline = 0
                f.write("%s, %8.2f\n" % (ts, usage,))
                self.curfile.write("%s, %.2f\n" % (mac, usage))
                #debug("MQTT put value in qpub")
                #msg = str('{"typ":"pwpower","ts":%d,"mac":"%s","power":%.2f}' % (ts, mac, usage))
                # qpub.put((self.ftopic("power", mac), msg, True))
            except ValueError:
                #print("%5d, " % (ts,))
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

    def log_recording(self, control, mac):
        """
        Failure to read recordings for a circle will prevent writing any new
        history data to the log files. Also the counter in the counter file is not
        updated. Consequently, at the next call (one hour later) reading the  
        history is retried.
        """
        fileopen = False
        if True: #control['savelog'].lower() == 'yes':
            info("%s: save log " % (mac,))
            try:
                c = self.circles[self.bymac[mac]]
            except:
                error("mac from controls not found in circles")
                return
            if not c.online:
                return
            
            #figure out what already has been logged.
            try:
                c_info = c.get_info()
                #update c.power fields for administrative purposes
                c.get_power_usage()
            except ValueError:
                return
            except (TimeoutException, SerialException) as reason:
                error("Error in log_recording() get_info: %s" % (reason,))
                return
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
                            return
                       
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
                            if dt == last_dt and c.production == True and i & 1:
                                tdt, twatt, twatt_hour = log[-1]
                                twatt+=watt
                                twatt_hour+=watt_hour
                                log[-1]=[tdt, twatt, twatt_hour]
                            else:
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
                    # #TODO: buffer is also len=4 for production?
                    # if len(buffer) == 4 or (len(buffer) == 2 and c.production == True):
                        # for i, (dt, watt, watt_hour) in enumerate(buffer):
                            # if not dt is None:
                                # #if the timestamp is identical to the previous, add production to usage
                                # #in case of hourly production logging, and end of daylightsaving, duplicate
                                # #timestamps can be present for two subsequent hours. Test the index
                                # #to be odd handles this.
                                # if dt == last_dt and c.production == True and i & 1:
                                    # tdt, twatt, twatt_hour = log[-1]
                                    # twatt+=watt
                                    # twatt_hour+=watt_hour
                                    # log[-1]=[tdt, twatt, twatt_hour]
                                # else:
                                    # log.append([dt, watt, watt_hour])
                                # debug("circle buffers: %s %d %s %d %d" % (mac, log_idx, dt.strftime("%Y-%m-%d %H:%M"), watt, watt_hour))
                            # last_dt = dt
                    # else:
                        # last -= 1
            except ValueError:
                return
                #error("Error: Failed to read power usage")
            except (TimeoutException, SerialException) as reason:
                #TODO: Decide on retry policy
                #do nothing means that it is retried after one hour (next call to this function).
                error("Error in log_recording() wile reading history buffers - %s" % (reason,))
                return
                
            debug("end   with first %d, last %d, idx %d, last_dt %s" % (first, last, idx, last_dt.strftime("%Y-%m-%d %H:%M")))

            #update last_log outside try block.
            #this results in a retry at the next call to log_recording
            c.last_log = last
            c.last_log_idx = idx
            c.last_log_ts = calendar.timegm((last_dt+timedelta(seconds=time.timezone)).utctimetuple())
            
            
            
            
            # if c.attr['loginterval'] <60:
                # dayfname = self.daylogfnames[mac]                
                # f=open(dayfname,'a')
            # else:
                # f=open(fname,'a')
                
            #initialisation to a value in the past.
            #Value assumes 6016 logadresses = 6016*4 60 minutes logs = 1002.n days
            #just set this several years back. Circles may have been unplugged for a while
            fileopen = False
            f = None
            prev_dt = datetime.now()-timedelta(days=2000)
            for dt, watt, watt_hour in log:
                if not dt is None:                
                    #calculate cumulative energy in Wh
                    c.cum_energy = c.cum_energy + watt_hour
                    watt = "%15.4f" % (watt,)
                    watt_hour = "%15.4f" % (watt_hour,)
                    ts_str = dt.isoformat()
                    # if epochf:
                    #     ts_str = str(calendar.timegm((dt+timedelta(seconds=time.timezone)).utctimetuple()))
                    # else:
                    #     ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    #print("%s, %s, %s" % (ts_str, watt, watt_hour))
                    
                    #use year folder determined by timestamps in circles
                    # yrfold = str(dt.year)+'/'
                    # if not os.path.exists(logpath):
                    #     os.makedirs(perpath+yrfold+actdir)
                    # if not os.path.exists(perpath+yrfold+logdir):
                    #     os.makedirs(perpath+yrfold+logdir)

                    today = get_now().date().isoformat()
                    fname = logpath + today + '_' + mac + '.log'
                    f = open(fname, 'a')
                    # if c.interval <60:
                    #     #log in daily file if interval < 60 minutes
                    #     if prev_dt.date() != dt.date():
                    #         #open new daily log file
                    #         if fileopen:
                    #             f.close()
                    #         ndate = dt.date().isoformat()
                    #         # persistent iso tmp
                    #         newfname= perpath + yrfold + logdir + logpre + ndate + '-' + mac + logpost
                    #         self.daylogfnames[mac]=newfname
                    #         f=open(newfname,'a')
                    # else:
                    #     #log in the yearly files
                    #     if prev_dt.year != dt.year:
                    #         if fileopen:
                    #             f.close()
                    #         newfname= perpath + yrfold + logdir + logpre + mac + logpost
                    #         self.logfnames[mac]=newfname
                    #         f=open(newfname,'a')
                    fileopen = True
                    prev_dt = dt                
                    f.write("%s, %s, %s\n" % (ts_str, watt, watt_hour))
                    #debug("MQTT put value in qpub")
                    msg = str('{"typ":"pwenergy","ts":%s,"mac":"%s","power":%s,"energy":%s,"cum_energy":%.4f,"interval":%d}' % (ts_str, mac, watt.strip(), watt_hour.strip(), c.cum_energy, c.interval))
                    # qpub.put((self.ftopic("energy", mac), msg, True))
            if not f == None:
                f.close()
                
            if fileopen:
                info("circle buffers: %s %s read from %d to %d" % (mac, c.attr['name'], first, last))
                
            #store lastlog addresses to file
            with open(self.lastlogfname, 'w') as f:
                for c in self.circles:
                    f.write("%s, %d, %d, %d, %.4f\n" % (c.mac, c.last_log, c.last_log_idx, c.last_log_ts, c.cum_energy))
                            
        return fileopen #if fileopen actual writing to log files took place
        
    def log_recordings(self):
        debug("log_recordings")
        for mac, idx in self.controlsbymac.iteritems():
            self.log_recording(self.controls[idx], mac)

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
                        idx=self.controlsbymac[c.mac]
                except ValueError:
                    continue
                except (TimeoutException, SerialException) as reason:
                    debug("Error in test_offline(): %s" % (reason,))
                    continue
                                
    def reset_all(self):
        #NOTE: Untested function, for example purposes
        print "Untested function, for example purposes"
        print "Aborting. Remove next line to continue"
        krak
        #
        #TODO: Exception handling
        for c in self.circles:
            if c.attr['name'] != 'circle+':
                print 'resetting '+c.attr['name']
                c.reset()
        for c in self.circles:
            if c.attr['name'] == 'circle+':
                print 'resetting '+c.attr['name']
                c.reset()
        print 'resetting stick'
        self.device.reset()
        print 'sleeping 60 seconds to allow devices to be reset themselves'
        time.sleep(60)

    def init_network(self):
        #NOTE: Untested function, for example purposes
        print "Untested function, for example purposes"
        print "Aborting. Remove next line to continue"
        krak
        #TODO: Exception handling        
        #
        #connect stick and circle+ (=network controller)
        #
        #First query status. An exception is expected due to an short 0011 response.
        #000A/0011
        try:
            self.device.status()
        except:
            pass
        success = False
        for i in range(0,10):
            print "Trying to connect to circleplus ..."
            #try to locate a circleplus on the network    
            #0001/0002/0003 request/responses
            try:
                success,cpmac = self.device.find_circleplus()
            except:
                #Not sure whether something should be handled
                pass
            #try to connect to circleplus on the network
            #0004/0005
            if success:
                try:
                    self.device.connect_circleplus()
                except:
                    pass
                #now unsolicited 0061 FFFD messages may arrive from circleplus
                #
                #now check for proper (long) status reply
                #000A/0011
                try:
                    self.device.status()
                    #stop the retry loop in case of success
                    break
                except:
                    success = False
            print "sleep 30 seconds for next retry ..."
            time.sleep(30)

    def connect_node_by_mac(self, newnodemac):
        #TODO: Exception handling
        #
        #the circleplus maintains a table of known nodes
        #nodes can be added to this table without ever having been on the network.
        #     s.join_node('mac', True), where s is the Stick object
        #nodes can also be removed from the table with methods:
        #     cp.remove_node('mac'), where cp is the circleplus object.
        #for demonstrative purposes read and print the table
        print self.circles[0].read_node_table()
      
        #Inform network that nodes are allowed to join the network
        #Nodes may start advertising themselves with a 0006 message.
        self.device.enable_joining(True)   
        time.sleep(5)
        #0006 may be received
        #Now add the given mac id to the circleplus node table
        self.device.join_node(newnodemac, True)            
        #now unsolicited 0061 FFFD messages may arrive from node if it was in a resetted state
        #
        #sleep to allow a resetted node to become operational
        time.sleep(60)
        #
        #test the node, assuming it is already in the configuration files
        try:
            print self.circles[self.bymac[newnodemac]].get_info()
        except:
            print 'new node not detected ...'        
        #
        #end the joining process
        self.device.enable_joining(False)
        #
        #Finally read and print the table of nodes again
        print self.circles[0].read_node_table()

        
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
        dst = time.localtime().tm_isdst

        self.sync_time()
        self.dump_status()
        self.log_recordings()
        
        # #SAMPLE: demonstration of connecting 'unknown' nodes
        # #First a known node gets removed and reset, and than
        # #it is added again by the connect_node_by_mac() method.
        # cp=self.circles[0]
        # c=self.circles[6]
        # try:
            # c.reset()
        # except:
            # pass
        # cp.remove_node(c.mac)
        # time.sleep(60)
        # cp.remove_node(c.mac)
        # time.sleep(2)
        # try:
            # print c.get_info()
        # except:
            # pass
        # self.connect_node_by_mac(c.mac)
        # try:
            # print c.get_info()
        # except:
            # pass

        offline = []
            
        circleplus = None
        for c in self.circles:
            try:
                if c.get_info()['type'] == 'circle+':
                    circleplus = c
            except:
                pass
        if circleplus != None:
            try:
                debug("joined node table: %s" % (circleplus.read_node_table(),))
            except:
                error("PWControl.run(): Communication error in read_node_table")
      
        #Inform network that nodes are allowed to join the network
        #Nodes may start advertising themselves with a 0006 message.
        try:
            self.device.enable_joining(True)
        except:
            error("PWControl.run(): Communication error in enable_joining")

        while 1:
            #check whether user defined configuration has been changed
            #when schedules are changed, this call can take over ten seconds!
            self.test_offline()
            self.poll_configuration()
            ##align with the next ten seconds.
            #time.sleep(10-datetime.now().second%10)
            #align to next 10 second boundary, while checking for input commands.
            ref = datetime.now()
            proceed_at = ref + timedelta(seconds=(10 - ref.second%10), microseconds= -ref.microsecond)
            while datetime.now() < proceed_at:
                #if mqtt: self.process_mqtt_commands()
                time.sleep(0.5)
            #prepare for logging values
            prev_dst = dst
            prev_day = day
            prev_hour = hour
            prev_minute = minute
            
            now = get_now()
            
            dst = time.localtime().tm_isdst
            day = now.day
            hour = now.hour
            minute = now.minute
            
            #read historic data only one circle per minute
            if minute != prev_minute:
                logrecs = True
            
            #get relays state just after each new quarter hour for circles operating a schedule.
            # if minute % 15 == 0 and now.second > 8:
            #     self.get_relays()
                
            #add configured unjoined nodes every minute.
            #although call is issued every hour
            if minute != prev_minute:
                self.connect_unknown_nodes()

            if day != prev_day:
                self.setup_actfiles()
            self.ten_seconds()
            new_offline = [c.short_mac() for c in self.circles if not c.online]
            if len(offline) > 0 and len(new_offline) == 0:
                print("all circles are back online")
            elif offline != new_offline:
                print("!!the following circles are offline: %s" % (new_offline,))
            offline = new_offline

            self.log_status()
            if hour != prev_hour:
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



init_logger(logpath+"pw-logger.log", "pw-logger")
log_level()

try:
    print(get_timestamp())
    main=PWControl()
    print("starting logging")
    main.run()
except:
    close_logcomm()
    raise
