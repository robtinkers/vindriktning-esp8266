"""
An micropython implementation of CPython's syslog wrapper and SysLogHandler APIs,
with a few handy extensions. Remember that if it's documented, it's a feature!

The only network transport is UDP because of the lower overhead on small devices.

References:
    https://www.rfc-editor.org/rfc/rfc3164
    https://www.rfc-editor.org/rfc/rfc5424
    https://docs.python.org/3/library/syslog.html
    https://docs.python.org/3/library/logging.html
    https://docs.python.org/3/library/logging.handlers.html#sysloghandler
    https://en.wikipedia.org/wiki/MIT_License

Copyright (c) 2022 'robtinkers'

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""
For any other dinosaurs looking at a modern Linux system and wondering how you
configure syslogd now, this way works in Raspbian 11...

Create /etc/rsyslog.d/remotelog.conf (without the indent):

    module(load="imudp")
    input(type="imudp" port="514")
    :fromhost-ip, startswith, "127." ~
    *.* /var/log/remote.log

Create /etc/logrotate.d/remotelog.conf (without the indent):

    /var/log/remote.log
    {
            rotate 10
            daily
            missingok
            ifempty
            nocompress
            nodelaycompress
            sharedscripts
            postrotate
                    /usr/lib/rsyslog/rsyslog-rotate
            endscript
    }

sudo systemctl restart rsyslog
sudo systemctl restart logrotate
tail -f /var/log/remote.log
"""

# heap size increased by ~6KB on ESP8266, as reported by
# import gc ; gc.collect() ; gc.mem_alloc() ; import usyslog ; z=usyslog.Handler() ; gc.collect() ; gc.mem_alloc()
# import gc,micropython ; gc.collect() ; micropython.mem_info() ; import usyslog ; z=usyslog.Handler() ; gc.collect() ; micropython.mem_info()

from micropython import const
import usocket # essential
import sys # if you *really* don't want to import sys, only minor code changes are needed

RFC = const(5424) # const(3164) or const(5424), might need to enable the code section in _syslog4() below

#### syslog facility constants

# pre-shifting is unconventional, but simpler to use and also how the CPython syslog wrapper does it.
# TODO: maybe comment out some of these? is a microcontroller really ever going to do UUCP?
LOG_KERN = const(0 << 3)
LOG_USER = const(1 << 3)
LOG_MAIL = const(2 << 3)
LOG_DAEMON = const(3 << 3)
LOG_AUTH = const(4 << 3)
LOG_SYSLOG = const(5 << 3)
LOG_LPR = const(6 << 3)
LOG_NEWS = const(7 << 3)
LOG_UUCP = const(8 << 3)
LOG_CRON = const(9 << 3)
LOG_AUTHPRIV = const(10 << 3)
## facilities 11-15 are in the RFC but not in CPython
#LOG_FTP = const(11 << 3)
#LOG_NTP = const(12 << 3)
#LOG_AUDIT = const(13 << 3)
LOG_CONSOLE = const(14 << 3) #EXTENSION: print to the console and never send over the network
#LOG_CLOCK = const(15 << 3)
LOG_LOCAL0 = const(16 << 3)
LOG_LOCAL1 = const(17 << 3)
LOG_LOCAL2 = const(18 << 3)
LOG_LOCAL3 = const(19 << 3)
LOG_LOCAL4 = const(20 << 3)
LOG_LOCAL5 = const(21 << 3)
LOG_LOCAL6 = const(22 << 3)
LOG_LOCAL7 = const(23 << 3)

#### syslog severity constants

LOG_EMERG = const(0)
LOG_ALERT = const(1)
LOG_CRIT = const(2)
LOG_ERR = const(3)
LOG_WARNING = const(4)
LOG_NOTICE = const(5)
LOG_INFO = const(6)
LOG_DEBUG = const(7)

#### syslog option constants (combine with bitwise or)

LOG_PID = const(0x01) # NOP
LOG_CONS = const(0x02) # "Write directly to the system console if there is an error while sending to the system logger."
LOG_ODELAY = const(0x04) # NYI
LOG_NDELAY = const(0x08) # NYI
LOG_NOWAIT = const(0x10) # NOP
LOG_PERROR = const(0x20) # "Also log the message to stderr."

#### more constants

# useful, also part of the SysLogHandler API
SYSLOG_UDP_PORT = const(514)

# added at the start of lines printed on the console (or written to a file if using perror redirection)
_severityprefixes = (
    # order is extremely important because a tuple, not a dict
    '[emergency] ',	# [0]
    '[alert] ',		# [1]
    '[critical] ',	# [2]
    '[error] ',		# [3]
    '[warning] ',	# [4]
    '[notice] ',	# [5]
    '[info] ',		# [6]
    '[debug] ',		# [7]
)

_INTERNAL_ERROR_SEVERITY = const(LOG_ERR)

######## Implement (most of) the syslog wrapper API ...

# This is the state shared between all (non-Handler) calls to this module.
# Public methods typically bundle this with their own arguments and pass to a private method.
# Each Handler object maintains its own seperate state to pass to the same private methods.
# The default settings for each Handler are a copy of this state when it is instantiated.
_state = {
    'hostname': '-',
    'ident': '-',
    'option': 0,
    'facility': LOG_USER,
    'logmask': 0, # note that the mask configures what is *ignored*
    'conmask': ~(LOG_EMERG|LOG_ALERT), # intentionally, the severities not used by the Handler
    'perror': sys.stderr, # can't be None, disable this feature by clearing the option bit
    'timestamp': '-' # either a fixed string, or a callable function
}

# these are shared so that this module can only ever use one socket
# (this is a *syslog* module, not a generic network-logging module)
_address = False # magic value, meaning no network logging
_info = None
_sock = None

## sample timestamp function to use as your callback
#def rfc5424timestamp(state): # updating 'state' has undefined behaviour
#    import time
#    t = time.gmtime()
#    return '%04d-%02d-%02dT%02d:%02d:%02dZ' % (t[0], t[1], t[2], t[3], t[4], t[5])

# note that if any value is None, then the associated state key will not be updated
# this allows us to pass in a method's named arguments with minimal processing
def _update_state(state, **kwargs):
    for k in kwargs:
        if kwargs[k] is None:
            pass
        elif k == 'address':
            global _address, _info, _sock
            _address = kwargs['address']
            _info = None
            _sock = None
        else:
            state[k] = kwargs[k]

#EXTENSION: how can you not have something called syslog.conf ?
def conf(**kwargs): # currently the only way to set 'address' 'hostname' 'conmask' 'perror' 'timestamp' in the basic API
    _update_state(_state, **kwargs)

# not a great API (which is why the similar one for conmask is disabled below)
# this is here for compatibility, if you just want to set the value use conf() instead
def setlogmask(mask):
    omask = state['logmask']
    if mask is not None and mask != 0: # as per the C API
        _state['logmask'] = mask
    return omask

#EXTENSION: is it actually an extension if it's disabled?
#def setconmask(mask):
#    omask = state['conmask']
#    if mask is not None and mask != 0: # copy setlogmask()'s API
#        _state['conmask'] = mask
#    return omask

def openlog(ident=None, option=None, facility=None):
    _update_state(_state, ident=ident, option=option, facility=facility)

def _close():
    global _sock, _info
    try: _sock.close()
    except: pass
    _sock = None
    _info = None

def closelog():
    _close()
    openlog(ident='-', option=0, facility=LOG_USER)
    #TODO: what about logmask (and conmask)? perror?

def _internal_exception_log(option, e, msg):
    if option & LOG_CONS:
        print(_severityprefixes[_INTERNAL_ERROR_SEVERITY] + 'syslog: ' + _EXCEPTION_FORMAT % (e.__class__.__name__, repr(e.value)), msg)

def _syslog4(state, facility, severity, msg):
    global _info, _sock

    facility = int(state['facility'] if facility == 0 else facility)
    severity = int(severity)
    timestamp = state['timestamp'] # sanity-checked later
    hostname = state['hostname'] # sanity-checked later
    ident = str(state['ident']).replace(' ','_') #EXTENSION: that .replace()
    option = int(state['option'])
#    logmask = int(state['logmask']) # not used in this method, must be checked by caller
    conmask = int(state['conmask'])
    perror = state['perror']

    if option & LOG_PERROR:
        try:
            perror.write((_severityprefixes[severity] + msg + '\n').encode('utf-8'))
        except Exception as e:
            _internal_exception_log(option, e, ' in perror.write() callback')
            pass

    #EXTENSION: automatically send LOG_CONSOLE and some severities to console
    if facility == LOG_CONSOLE or (severity & conmask == 0):
        print(_severityprefixes[severity] + msg)

    if facility == LOG_CONSOLE or _address is False:
        return

    if _info is None:
        try:
            #EXTENSION: tuple not required, can just use a string and assume the port number
            if isinstance(_address, tuple):
                _info = usocket.getaddrinfo(_address[0], _address[1])[0][-1]
            else:
                _info = usocket.getaddrinfo(_address, SYSLOG_UDP_PORT)[0][-1]
        except Exception as e:
            _internal_exception_log(option, e, ' in getaddrinfo()')
            return

    if _sock is None:
        try:
            _sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
        except Exception as e:
            _internal_exception_log(option, e, ' in socket(AF_INET,SOCK_DGRAM)')
            return

## "You think you do but you don't"
#    if callable(hostname):
#        try:
#            hostname = hostname(state)
#        except Exception as e:
#            _internal_exception_log(option, e, ' in hostname() callback')
#            hostname = ''

    # In theory, most of this code section will be optimised away. TODO: check this!
    if False:
        pass
#    elif RFC == 3164: # RFC3164
#        if callable(timestamp):
#            try:
#                timestamp = str(timestamp(state)) # RC3164 timestamps aren't trivial to sanity-check
#            except Exception as e:
##                _internal_exception_log(option, e, ' in timestamp() callback')
#                timestamp = ''
#        if hostname == '':
#            hostname = '-'
#        if ident != '':
#            ident = str(ident) + ': '
#        data = '<%d>%s %s %s%s' % (facility|severity, timestamp, hostname, ident, msg)
#        data = data.encode()
    elif RFC == 5424: # RFC5424
        if callable(timestamp):
            try:
                timestamp = str(timestamp(state))
                if int(timestamp[:4]) < 2023: # simple sanity-check; or just check <= 1970 to make sure unix epoch isn't leaking
                    timestamp = ''
            except Exception as e:
#                _internal_exception_log(option, e, ' in timestamp() callback')
                timestamp = ''
        if timestamp == '':
            timestamp = '-'
        if hostname == '':
            hostname = '-'
        if ident == '':
            ident = '-'
        data = '<%d>1 %s %s %s - - - %s' % (facility|severity, timestamp, hostname, ident, msg)
        data = data.encode('utf-8')
    else:
        pass # 'data' is undefined and will throw an exception in the next line

    try:
        _sock.sendto(data, _info)
    except Exception as e:
        _internal_exception_log(option, e, ' in sendto()')
        # throw away the socket and get a new one next time
        try: _sock.close()
        except: pass
        _sock = None

#FEATURE: pri is optional in CPython, but required here
def syslog(pri, msg):
    facility = (pri & ~0x07)
    severity = (pri &  0x07)

    logmask = int(state['logmask'])
    if severity & logmask:
        return

    _syslog4(_state, facility, severity, msg)

######## Implement (part of) the LogHandler API, hopefully just enough to be useful ...

#### LogHandler level constants

#FEATURE: these are not the LogHandler defaults, and are ordered the other way around
EMERG = const(LOG_EMERG) #EXTENSION: not a default LogHandler level
ALERT = const(LOG_ALERT) #EXTENSION: not a default LogHandler level
CRITICAL = const(LOG_CRIT)
ERROR = const(LOG_ERR)
WARNING = const(LOG_WARNING)
NOTICE = const(LOG_NOTICE) #EXTENSION: not a default LogHandler level
INFO = const(LOG_INFO)
DEBUG = const(LOG_DEBUG)
NOTSET = const(LOG_DEBUG+1)

#### more constants

_DEFAULT_LEVEL = const(WARNING)
_EXCEPTION_LEVEL = const(ERROR)
_EXCEPTION_FORMAT = '%s: %s' # the same as Python's default

class Handler():

    #FEATURE: constructor doesn't take a socktype argument, and we only support UDP network traffic anyway
    #EXTENSION: can configure more syslog values per Handler() not just the facility
    def __init__(self, address=None, facility=None, **kwargs):
        self._state = _state.copy()
        _update_state(self._state, address=address, facility=facility, level=_DEFAULT_LEVEL)
        _update_state(self._state, **kwargs)

    #EXTENSION: most useful to switch between LOG_CONSOLE and LOG_SOMETHINGELSE
    def setFacility(self, facility):
        _update_state(self._state, facility=facility)

    def setLevel(self, level):
        _update_state(self._state, level=level)

    def close(self):
        _close()

    def log(self, level, msg, *args):
        if level > self._state['level']:
            return
        _syslog4(self._state, 0, level, msg % args)

#### could split into a seperate sub-class here

    def debug(self, msg, *args):
        self.log(DEBUG, msg, *args)

    def info(self, msg, *args):
        self.log(INFO, msg, *args)

    #EXTENSION: NOTICE isn't a default LogHandler level
    def notice(self, msg, *args):
        self.log(NOTICE, msg, *args)

    def warning(self, msg, *args):
        self.log(WARNING, msg, *args)

    def error(self, msg, *args):
        self.log(ERROR, msg, *args)

    def critical(self, msg, *args):
        self.log(CRITICAL, msg, *args)

# rather than create convenience functions for alert/emerg, call .log() directly
# why not for these, but for notice above? because in the default setup, EMERG and ALERT
# are also printed to the console (see 'conmask'), so if you want that side-effect
# then you'll have to forego the convenience functions. they are also outside the
# usual range of LogHandler level values. TODO: explain this all better!
# maybe capitalise and have .ALERT() and .EMERG() ?
#
#    def alert(self, msg, *args):
#        self.log(ALERT, msg, *args)
#
#    def emerg(self, msg, *args):
#        self.log(EMERG, msg, *args)

#### could split into a seperate sub-class here

    # the default API is not good, but we have to support it for compatibility
    # but we also add a couple of extensions, allowing us to do stuff like
    #   log.exception(e, ' in some_function(%s,%s)', arg1, arg2)    # everything in one log entry
    #   log.exception('some_function(%s,%s) failed', arg1, arg2, e) # no named argument for the simple case
    def exception(self, msg, *args, **kwargs):
        exc_info = kwargs.get('exc_info')

        if exc_info is not None: # if 'exc_info' was specified, then proceed as usual
            if isinstance(exc_info, BaseException):
                exc = exc_info
            elif isinstance(exc_info, tuple) and len(exc_info) > 1 and isinstance(exc_info[1], BaseException):
                exc = exc_info[1]
            elif exc_info: # 'exc_info' was specified and non-falsey, so default behaviour
                exc = True
            else: # 'exc_info' was specified and falsey
                exc = False
        elif isinstance(msg, BaseException): #EXTENSION: exception as the first argument (i.e. 'msg') for everything in one log entry
            if args:
                self.log(_EXCEPTION_LEVEL, _EXCEPTION_FORMAT + args[0], *((msg.__class__.__name__, repr(msg.value)) + args[1:]))
            else:
                self.log(_EXCEPTION_LEVEL, _EXCEPTION_FORMAT, *(msg.__class__.__name__, repr(msg.value)))
            return
        elif args and isinstance(args[-1], BaseException): #EXTENSION: exception as the last argument (i.e. no named argument)
            exc = args[-1]
            args = args[:-1]
        else: # 'exc_info' was not specified, so default behaviour
            exc = True

        if exc is True:
            try: exc = sys.exc_info()[1] # How well supported is this on micropython?
            except: exc = False

        self.log(_EXCEPTION_LEVEL, msg, *args)
        if exc is not False:
            self.log(_EXCEPTION_LEVEL, _EXCEPTION_FORMAT, exc.__class__.__name__, repr(exc.value))
