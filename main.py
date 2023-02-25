import network, sys, time
import random
from umqtt import simple # TODO: investigate umqtt.robust
import usyslog
from pm1006 import PM1006

import config
print()

# Extend the basic UMQTT client with a couple of helpers to keep our own code cleaner
class MQTTClient(simple.MQTTClient):

    def isconnected(self):
        return bool(self.sock is not None)

    def disconnect(self):
        try: super().disconnect()
        except: pass
        self.sock = None

# Helper routine to keep our own code cleaner. NOTE: no timeout, may hang indefinitely
def wlan_connect():
    # wlan.status start off 0, then 1 while trying to connect, finally 5 when connected
    while True:
        wlan.active(True)
        wlan.connect(config.wifi_network, config.wifi_password)
        for i in range(0, 10):
            time.sleep(1)
            if wlan.isconnected():
                time.sleep(5) # TODO: check if this is actually necessary
                return True

# Start with local logging
log = usyslog.SyslogClient(config.syslog_address)
log.openlog('vindriktning', usyslog.LOG_PERROR|usyslog.LOG_CONS, usyslog.LOG_CONSOLE, config.machine_id)

# Connect to the network
network.WLAN(network.AP_IF).active(False)
wlan = network.WLAN(network.STA_IF)
try:
    wlan_connect()
except Exception as e:
    log.critical('Exception %s:%s while connecting to network' % (type(e).__name__, e.args))

# Switch to remote logging
log.openlog('vindriktning', usyslog.LOG_PERROR|usyslog.LOG_CONS, usyslog.LOG_DAEMON, config.machine_id)
log.info('Started')

# Set up the PM1006 sensor
pm1006 = PM1006(config.pm1006_rxpin, loghandler=log)

# Set up UMQTT
mqtt = MQTTClient(config.mqtt_client_id, config.mqtt_broker,
                  user=config.mqtt_username, password=config.mqtt_password,
                  ssl=False) # FIXME: test with SSL, add config option (also port number)

##
## MAIN LOOP
##

random.seed(None)

#next_publish_time = time.time() + 100 + random.getrandbits(8) # approx. two to six minutes
next_publish_time = time.time() + 45

readings = []

while True:

    try:

        ## READ VNOW

        vnow = pm1006.read_raw()
        if vnow is None:
            vnow = []
        vnow.sort()

        if len(vnow):
            vnow = vnow[len(vnow)//2] # median
            if config.pm1006_adjust_add is not None:
                vnow += config.pm1006_adjust_add
            if vnow < 0:
                vnow = 0
            readings.append(vnow)
        else:
            vnow = None
            readings.append(-1)

        ## CALC OTHER VALUES

        readings = readings[-120:] # we get a fresh batch of readings every ~30 seconds, so always keep one hour

        v60m = readings.copy()
        v60m.sort()
        while len(v60m) and v60m[0] == -1: v60m.pop(0) # drop any negative values used as padding
        if len(v60m):
            v60m = sum(v60m) / len(v60m) # mean (of medians)
        else:
            v60m = None

        v05m = readings[-10:] # no .copy() needed; this is 10 batches (~5 minutes)
        v05m.sort()
        while len(v05m) and v05m[0] == -1: v05m.pop(0) # drop any negative values used as padding
        if len(v05m):
            v05m = v05m[len(v05m)//2] # median (of medians)
        else:
            v05m = None

        v90s = readings[-3:] # no .copy() needed; this is 3 batches (so median will filter out one extreme batch)
        v90s.sort()
        while len(v90s) and v90s[0] == -1: v90s.pop(0) # drop any negative values used as padding
        if len(v90s):
            v90s = v90s[len(v90s)//2] # median (of medians)
        else:
            v90s = None

        log.debug('vnow=%s / v90s=%s / v05m=%s / v60m=%s' % (repr(vnow),repr(v90s),repr(v05m),repr(v60m)))

        ## TIME?

        if time.time() < next_publish_time:
            continue
#        next_publish_time = time.time() + 300 + random.getrandbits(6) # approx. five to six minutes
        next_publish_time = time.time() + 45

        ## DATA?

        pmvt = v90s

        if pmvt is None:
            continue

        ## CONNECT

        if not wlan.isconnected():
            log.info('Connecting to network %s' % (repr((config.wifi_network, '****' if config.wifi_password else config.wifi_password)),))
            try:
                wlan_connect()
                log.debug('Connected to network %s' % (repr(wlan.ifconfig()),))
            except Exception as e:
                log.critical('Exception %s:%s while connecting to network' % (type(e).__name__, e.args))
        else:
            log.debug('Already connected to network (wlan.status=%s)' % (repr(wlan.status()),))

        if not wlan.isconnected():
            log.debug('Ignoring broker while not connected to network')
            continue
        else:
            log.info('Connecting to broker %s' % (repr((mqtt.server, mqtt.port)),))
            try:
                mqtt.connect() # default is clean_session=True
                log.debug('Connected to broker %s' % (repr(mqtt.sock),))
            except Exception as e:
                log.critical('Exception %s:%s while connecting to broker' % (type(e).__name__, e.args))
                continue

        ## PUBLISH

        if config.mqtt_topic_pmvt is not None:

                log.info('Publishing %s' % (repr((config.mqtt_topic_pmvt, pmvt))))
                try:
                    mqtt.publish(config.mqtt_topic_pmvt, '%.2f' % (pmvt,), retain=True)
                    log.debug('Publish success!')
                except:
                    log.critical('Exception %s:%s while publishing (%d seconds since last success)' % (type(e).__name__, e.args, time.time() - mqtt_last_success))

        log.debug('Disconnecting from broker')
        mqtt.disconnect()

    except Exception as e:
        log.syslog(usyslog.LOG_ALERT, 'UNHANDLED EXCEPTION %s:%s' % (type(e).__name__, e.args))
