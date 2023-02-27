# Copy this file to 'config.py' and edit as required

from machine import unique_id
from ubinascii import hexlify

print()
machine_id = hexlify(unique_id()).decode()
print('MACHINE_ID = %s' % machine_id)

wifi_network        = 'My-WiFi'
wifi_password       = 'My-Pass'

syslog_address      = None

mqtt_client_id      = machine_id
mqtt_broker         = 'mqtt.example.com'
mqtt_username       = 'mqtt_username'
mqtt_password       = 'mqtt_password'

mqtt_topic_pmvt     = '%s/feeds/my.feed' % (mqtt_username,)
pm1006_rxpin        = 13
pm1006_filter       = None
pm1006_smooth       = min
