import config
from umqtt.simple import MQTTClient # TODO: investigate umqtt.robust
import network, sys, time
import os # for offline logging
import gc # for memory usage
import usyslog
from pm1006 import PM1006_Sensor

logger = usyslog.SyslogClient(config.syslog_address)
logger.openlog('vindriktning', usyslog.LOG_PERROR|usyslog.LOG_CONS, usyslog.LOG_DAEMON, config.machine_id)

pm1006 = PM1006_Sensor(config.pm1006_rxpin, logger=logger)

network.WLAN(network.AP_IF).active(False)
wlan = network.WLAN(network.STA_IF)

mqtt = MQTTClient(config.mqtt_client_id, config.mqtt_server,
                  user=config.mqtt_username, password=config.mqtt_password,
                  ssl=False) # FIXME: test with SSL, add config option (also port number)

try:
    offline_publishing = True
    with open('/offline.dat','r') as offline_dat:
        offline_seq = int(offline_dat.readline())
    offline_seq = (offline_seq+1) % 10
    for offline_h in range(0,24):
        offline_fn = '/off_%01d_%02d.log' % (offline_seq, offline_h)
        try:
            os.remove(offline_fn)
        except OSError: # file not found
            pass
except ValueError: # file empty (or corrupt)
    offline_publishing = True
    for offline_fn in os.listdir('/'):
        if offline_fn[:4] == 'off_' and offline_fn[-4:] == '.log':
            os.remove(offline_fn)
    offline_seq = 0
except OSError: # file not found
    offline_publishing = False

if offline_publishing:
    with open('/offline.dat','w') as offline_dat:
        offline_dat.write(str(offline_seq))
    t = time.gmtime() # we don't do timezones
    offline_h = t[3]
    offline_fn = '/off_%01d_%02d.log' % (offline_seq, offline_h)
    offline_log = open(offline_fn, 'w')
    del t


##
##
##

while True:

    ## READ PMVT

    pmvt = pm1006.read(config.pm1006_smoothing, config.pm1006_adjust_add, config.pm1006_adjust_mul)
    logger.info('PMVT = %s' % (repr(pmvt),))

    ## CONNECT

    if not wlan.isconnected():
        try:
            s = 1
            while True:
                if s == 1:
                    logger.info('Connecting to network %s' % (repr((config.wifi_network, '****' if config.wifi_password else config.wifi_password)),))
                    wlan.active(True)
                    wlan.connect(config.wifi_network, config.wifi_password)
                for i in range(0, s):
                    time.sleep(1)
                    if wlan.isconnected():
                        i = True
                        break
                if i is True:
                    break
                s = (s % 5) + 1
            logger.info('Connected to network %s' % (repr(wlan.ifconfig()),))
            time.sleep(5)
            del i, s
        except Exception as e:
            logger.critical('Exception %s:%s while connecting to network' % (type(e).__name__, e.args))
    else:
        logger.debug('Already connected to network %s' % (repr(wlan.ifconfig()),))

    if not wlan.isconnected():
        logger.warning('Ignoring broker while not connected to network')
    elif mqtt.sock is None:
        try:
            logger.info('Connecting to broker % s' % (repr((mqtt.server,mqtt.port)),))
            mqtt.connect() # default is clean_session=True
            logger.info('Connected to broker')
        except Exception as e:
            logger.critical('Exception %s:%s while connecting to broker' % (type(e).__name__, e.args))
            mqtt.sock = None
            try: wlan.disconnect()
            except: pass
    else:
        logger.debug('Already connected to broker')

    ## PUBLISH

    if wlan.isconnected() and mqtt.sock is not None and config.mqtt_topic_pmvt is not None:
        if pmvt is not None:
            try:
                logger.info('Publishing to %s' % (repr(config.mqtt_topic_pmvt),))
                mqtt.publish(config.mqtt_topic_pmvt, '%.2f' % (pmvt,), retain=True)
            except Exception as e:
                logger.critical('Exception %s:%s while publishing to %s' % (type(e).__name__, e.args, repr(config.mqtt_topic_pmvt)))
                try: mqtt.disconnect() ; mqtt.sock = None
                except: pass
        else:
            try:
                logger.info('Pinging broker')
                mqtt.ping()
            except Exception as e:
                logger.critical('Exception %s:%s while pinging broker' % (type(e).__name__, e.args))
                try: mqtt.disconnect() ; mqtt.sock = None
                except: pass

    if not wlan.isconnected() and offline_publishing:
        try:
            t = time.gmtime()
            if offline_h != t[3]:
                offline_h = t[3]
                offline_fn = '/off_%01d_%02d.log' % (offline_seq, offline_h)
                offline_log = open(offline_fn, 'w')
            offline_log.write('%02d:%02d:%02d\tPMVT\t%.2f\n' % (t[3], t[4], t[5], pmvt))
            del t
        except:
            logger.critical('Exception %s:%s while publishing to file' % (type(e).__name__, e.args))

    # memory

    logger.debug('gc.mem_free() = %d' % (gc.mem_free(),))

    # LEDs

    if len(pm1006.adjusted_ringbuf):
        logger.debug('PMVT hourly average = %f' % ((sum(pm1006.adjusted_ringbuf) / len(pm1006.adjusted_ringbuf)),))
        # TODO: ...
        # if hourly average > threshold then red
        # elif current value > threshold then yellow
        # else green
