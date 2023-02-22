import config
from umqtt import simple # TODO: investigate umqtt.robust
import network, sys, time
import os # for offline logging
import usyslog
from pm1006 import PM1006

# Extend the basic UMQTT client with a couple of helpers to keep our own code cleaner
class MQTTClient(simple.MQTTClient):

    def isconnected(self):
        return bool(self.sock is not None)

    def disconnect(self):
        super().disconnect()
        self.sock = None

# Helper routine to keep our own code cleaner. NOTE: no timeout, may hang indefinitely
def wlan_connect():
    while True:
        wlan.active(True)
        wlan.connect(config.wifi_network, config.wifi_password)
        for i in range(0, 5):
            time.sleep(1)
            if wlan.isconnected()
                time.sleep(5)
                return True

# Connect to the network asap so that remote logging works
network.WLAN(network.AP_IF).active(False)
wlan = network.WLAN(network.STA_IF)
try: wlan_connect()
except: pass # we don't have a working logger yet, but any errors will be reported in the main loop

# Set up remote logging
logger = usyslog.SyslogClient(config.syslog_address)
logger.openlog('vindriktning', usyslog.LOG_PERROR|usyslog.LOG_CONS, usyslog.LOG_DAEMON, config.machine_id)
logger.info('Started')

# Set up UMQTT
mqtt = MQTTClient(config.mqtt_client_id, config.mqtt_broker,
                  user=config.mqtt_username, password=config.mqtt_password,
                  ssl=False) # FIXME: test with SSL, add config option (also port number)

# Set up the PM1006 sensor
pm1006 = PM1006(config.pm1006_rxpin, logger=logger,
                add=config.pm1006_adjust_add,
                mul=config.pm1006_adjust_mul,
                smoothing=config.pm1006_smoothing)

##
## MAIN LOOP
##

mqtt_last_success = time.time()

while True:

    ## READ PMVT

    pmvt = pm1006.read()
    logger.info('PMVT = %s' % (repr(pmvt),))

    # LEDs

    if len(pm1006.adjusted_ringbuf):
        logger.debug('PMVT hourly average = %f' % ((sum(pm1006.adjusted_ringbuf) / len(pm1006.adjusted_ringbuf)),))
        # TODO: this is very much a work in progress, waiting on the hardware side to be done first
        # e.g.
        # if hourly average > threshold then red
        # elif current value > threshold then yellow
        # else green

    ## CONNECT

    if not wlan.isconnected():
        logger.info('Connecting to network %s' % (repr((config.wifi_network, '****' if config.wifi_password else config.wifi_password)),))
        try:
            wlan_connect()
            logger.info('Connected to network %s' % (repr(wlan.ifconfig()),))
        except Exception as e:
            logger.critical('Exception %s:%s while connecting to network' % (type(e).__name__, e.args))
    else:
        logger.debug('Already connected to network %s' % (repr(wlan.ifconfig()),))

    if not wlan.isconnected():
        logger.warning('Ignoring broker while not connected to network')
    elif not mqtt.isconnected():
        logger.info('Connecting to broker % s' % (repr((mqtt.server,mqtt.port)),))
        try:
            mqtt.connect() # default is clean_session=True
            logger.info('Connected to broker')
        except Exception as e:
            logger.critical('Exception %s:%s while connecting to broker' % (type(e).__name__, e.args))
    else:
        logger.debug('Already connected to broker')

    if not wlan.isconnected() or not mqtt.isconnected():
        continue

    ## PUBLISH

    if config.mqtt_topic_pmvt is not None:

        if pmvt is not None:
            logger.info('Publishing to %s' % (repr(config.mqtt_topic_pmvt),))
            try:
                mqtt.publish(config.mqtt_topic_pmvt, '%.2f' % (pmvt,), retain=True)
                mqtt_last_success = time.time()
            except Exception as e:
                logger.critical('Exception %s:%s while publishing to %s' % (type(e).__name__, e.args, repr(config.mqtt_topic_pmvt)))
        else:
            logger.info('Pinging broker')
            try:
                mqtt.ping()
                mqtt_last_success = time.time()
            except Exception as e:
                logger.critical('Exception %s:%s while pinging broker' % (type(e).__name__, e.args))

        if time.time() - mqtt_last_success > 100:
            try: mqtt.disconnect()
            except: pass

        if time.time() - mqtt_last_success > 200:
            try: wlan.disconnect()
            except: pass
            mqtt_last_success = time.time() # fake it until you make it
