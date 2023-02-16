# Copy this file to 'config.py' and edit as required

from machine import unique_id
from ubinascii import hexlify

_machine_id = hexlify(unique_id()).decode()
#print('MACHINE_ID = %s' % _machine_id)

wifi_network  = 'My-WiFi'
wifi_password = 'My-Pass'

mqtt_client_id  = _machine_id
mqtt_server     = 'mqtt.example.com'
mqtt_username   = 'mqtt_username'
mqtt_password   = 'mqtt_password'
mqtt_topic_pmvt = '%s/feeds/my.feed' % (mqtt_username,)

pm1006_rxpin = 13
pm1006_adjust_add = None # add first
pm1006_adjust_mul = None # then multiply
pm1006_smoothing = None # exponential smoothing [0,1.0)

del _machine_id
