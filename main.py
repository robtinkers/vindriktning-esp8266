import config
from machine import Pin, SoftUART
from umqtt.simple import MQTTClient # TODO: investigate umqtt.robust
import network, sys, time
import os # for offline logging
import gc # for memory usage

import usyslog
logger = usyslog.uSysLog(facility=usyslog.LOG_CONSOLE)

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

network.WLAN(network.AP_IF).active(False)
wlan = network.WLAN(network.STA_IF)

mqtt = MQTTClient(config.mqtt_client_id, config.mqtt_server,
                  user=config.mqtt_username, password=config.mqtt_password,
                  ssl=False) # FIXME: test with SSL, add config option (also port number)

# tx is required but not used, doesn't even need to be connected
# timeout must be over 2 seconds to capture a full Vindriktning cycle with one read()
# but the smaller the timeout the better to maximise time left for non-UART stuff
# this variable must start with double underscore because of a Thonny bug
__pm1006_uart = SoftUART(baudrate=9600, rx=Pin(config.pm1006_rxpin), tx=Pin(0), timeout=3000)

def __sniff_pm1006_uart():
    global logger, __pm1006_uart

    logger.info('Waiting for UART')
    while True:
        try:
            print ('.', end='')
            data = __pm1006_uart.read()
        except Exception as e:
            print()
            logger.critical('Exception %s:%s while reading UART' % (type(e).__name__, e.args))
            return None
        if data is None:
            continue
        break
    print()
    logger.info('Read from UART (%d bytes)' % (len(data),))

    readings = []
    for offset in range(0,len(data),20):
        if offset+20 > len(data):
            logger.warning('Partial frame at %d, ignoring reading' % (offset,))
            break
        if data[offset+0] != 22 or data[offset+1] != 17 or data[offset+2] != 11:
            # TODO: probably missed a symbol; in theory, we could resync on magic
            logger.warning('Bad magic at %d, ignoring reading' % (offset,))
            continue # or break?
        if sum(data[offset:offset+20]) % 256 != 0:
            logger.warning('Bad checksum at %d, ignoring reading' % (offset,))
            continue
        df3 = data[offset+5]
        df4 = data[offset+6]
        readings.append(df3*256+df4)

    logger.info('UART readings are %s' % (repr(readings),))

    return readings

__pm1006_adjusted_ringbuf = []
__pm1006_adjusted_ringptr = None
__pm1006_last_adjusted = None
__pm1006_last_smoothed = None

def read_pm1006(smoothing=0, add=None, mul=None):
    global logger, __pm1006_adjusted_ringbuf, __pm1006_adjusted_ringptr, __pm1006_last_adjusted, __pm1006_last_smoothed

    readings = __sniff_pm1006_uart()

    if readings is None:
        # we already logged something
        __pm1006_last_adjusted = None
        __pm1006_last_smoothed = None
        return None

    if len(readings) < 1:
        logger.critical('UART readings not found')
        __pm1006_last_adjusted = None
        __pm1006_last_smoothed = None
        return None

    # the sensor readings can be very spiky, so we do a lot of smoothing...

    # 1.
    if len(readings) > 6:
        readings = readings[-6:]
    readings.sort()

#    # 2.
#    if readings[-1] == 0:
#        logger.warning('UART readings all zero')
#        __pm1006_last_adjusted = None
#        __pm1006_last_smoothed = None
#        return None

    # 3.
    if readings[-1] >= readings[0] + 100:
        logger.warning('UART readings too volatile')
        __pm1006_last_adjusted = None
        __pm1006_last_smoothed = None
        return None

    # 4.
    if (len(readings) >= 6):
        pm1006 = float(readings[2] + readings[3]) / 2
    elif (len(readings) == 5):
        pm1006 = float(readings[2])
    elif (len(readings) == 4):
        pm1006 = float(readings[1] + readings[2]) / 2
    elif (len(readings) == 3):
        pm1006 = float(readings[1])
    else:
        logger.error('UART readings missing')
        __pm1006_last_adjusted = None
        __pm1006_last_smoothed = None
        return None

    #
    if add is not None:
        pm1006 = pm1006 + add
    if mul is not None:
        pm1006 = pm1006 * mul
    if pm1006 < 0:
        pm1006 = 0.00

    # keep a ring buffer of the latest 120 values (adjusted but not smoothed)
    # this is ~60 minutes worth, assuming no read errors
    # TODO: is the read error rate high enough that it's worth tracking timestamps?
    if len(__pm1006_adjusted_ringbuf) < 120:
        __pm1006_adjusted_ringbuf.append(pm1006)
    else:
        if __pm1006_adjusted_ringptr is None:
            __pm1006_adjusted_ringptr = 0
        else:
            __pm1006_adjusted_ringptr = (__pm1006_adjusted_ringptr+1) % 120
        __pm1006_adjusted_ringbuf[__pm1006_adjusted_ringptr] = pm1006

    # 5.
    if __pm1006_last_adjusted is not None and pm1006 > __pm1006_last_adjusted:
        (pm1006, __pm1006_last_adjusted) = (__pm1006_last_adjusted, pm1006)
    else:
        __pm1006_last_adjusted = pm1006

    # 5.
    if __pm1006_last_smoothed is not None and smoothing is not None:
        pm1006 = (__pm1006_last_smoothed * smoothing) + (pm1006 * (1 - smoothing))
    __pm1006_last_smoothed = pm1006

    return pm1006

##
##
##

while True:

    ## READ PMVT

    pmvt = read_pm1006(config.pm1006_smoothing, config.pm1006_adjust_add, config.pm1006_adjust_mul)
    logger.info('PMVT = %s' % (repr(pmvt),))

    ## READ OTHER SENSORS

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

    # LEDs

    if len(__pm1006_adjusted_ringbuf):
        logger.debug('PMVT hourly average = %f' % ((sum(__pm1006_adjusted_ringbuf) / len(__pm1006_adjusted_ringbuf)),))
        # TODO: ...
        # if hourly average > threshold then red
        # elif current value > threshold then yellow
        # else green
