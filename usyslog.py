"""
A partial implementation of CPython's syslog wrapper and SysLogHandler APIs, for micropython.

Timestamps and unicode are not supported, but there are a few handy extensions.

Remember it's not a bug if it's documented, it's a feature!

References:
    https://www.rfc-editor.org/rfc/rfc5424
    https://docs.python.org/3/library/syslog.html
    https://docs.python.org/3/library/logging.html
    https://docs.python.org/3/library/logging.handlers.html#sysloghandler
    https://en.wikipedia.org/wiki/MIT_License

Copyright (c) 2022 "robtinkers"

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
# heap size increased by 3520 bytes on a Pi Pico, as reported by
# import gc ; gc.collect() ; gc.mem_alloc() ; import usyslog ; z=usyslog.Handler() ; gc.collect() ; gc.mem_alloc()
# in comparison, kfricke's usyslog adds 2576 bytes

from micropython import const
import usocket

#### syslog facility constants
# Most of these don't apply to microcontrollers, so we save a little memory by commenting them out.
# Pre-shifting is unconventional, but simpler to use and also how the CPython syslog wrapper does it.

#LOG_KERN = const(0 << 3)
LOG_USER = const(1 << 3)
#LOG_MAIL = const(2 << 3)
LOG_DAEMON = const(3 << 3)
#LOG_AUTH = const(4 << 3)
#LOG_SYSLOG = const(5 << 3)
#LOG_LPR = const(6 << 3)
#LOG_NEWS = const(7 << 3)
#LOG_UUCP = const(8 << 3)
#LOG_CRON = const(9 << 3)
#LOG_AUTHPRIV = const(10 << 3)
#LOG_FTP = const(11 << 3)
## facilities 12-15 are in the RFC (but not generally in syslog.h)
#LOG_NTP = const(12 << 3)
#LOG_AUDIT = const(13 << 3)
LOG_CONSOLE = const(14 << 3) # EXTENSION: always print to the console and never send over the network
#LOG_CLOCK = const(15 << 3)
LOG_LOCAL0 = const(16 << 3)
LOG_LOCAL1 = const(17 << 3)
LOG_LOCAL2 = const(18 << 3)
LOG_LOCAL3 = const(19 << 3)
LOG_LOCAL4 = const(20 << 3)
LOG_LOCAL5 = const(21 << 3)
LOG_LOCAL6 = const(22 << 3)
LOG_LOCAL7 = const(23 << 3)

#### syslog priority constants

LOG_EMERG = const(0)
LOG_ALERT = const(1)
LOG_CRIT = const(2)
LOG_ERR = const(3)
LOG_WARNING = const(4)
LOG_NOTICE = const(5)
LOG_INFO = const(6)
LOG_DEBUG = const(7)

#### option constants (combine with bitwise or)

LOG_PERROR = const(0x20) # "log to stderr as well" [i.e. to the console by default]
LOG_CONS = const(0x02) # "log on the console if errors in sending"

#### more constants

# useful, also part of the SysLogHandler API
SYSLOG_UDP_PORT = const(514)

# added at the start of lines printed on the console (or written to a file if using perror redirection)
_priorityprefixes = (
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

################

#### Implement (most of) the syslog wrapper API ...

# monkey patch this if you want timestamps
def _timestamp():
    return '-'

_state = {
    'hostname': '-',
    'ident': '-',
    'option': 0,
    'facility': LOG_USER,
    'logmask': 0, # note that the mask configures what is *ignored*
    'conmask': ~(LOG_EMERG|LOG_ALERT),
    'stderr': None
}

# these are shared so that this module can only ever use one socket
_address = False # magic value, meaning no network logging
_info = None
_sock = None

def _update_state(state, **kwargs):
    for k in kwargs:
        if kwargs[k] is None:
            pass
        elif k == 'address':
            global _address, _info, _sock
            _address = _state['address']
            _info = None
            _sock = None
        else:
            state[k] = kwargs[k]

# EXTENSION: syslog.conf()
def conf(**kwargs):
    _update_state(_state, kwargs)

def openlog(ident=None, option=None, facility=None):
    conf(ident=ident, option=option, facility=facility)

def closelog():
    global _sock, _info
    try: _sock.close()
    except: pass
    _sock = None
    _info = None
    openlog(ident='-', option=0, facility=LOG_USER)

# EXTENSION: redirect "stderr" so that LOG_PERROR logs to a file instead of the console
# (actually, anything with a write method will work) or None to disable this feature
def setstderr(fh):
    _state['stderr'] = fh

def setlogmask(mask):
    omask = state['logmask']
    if mask != 0: # as per the C api
        _state['logmask'] = int(mask) # raises exception if mask isn't an int
    return omask

def setconmask(mask):
    omask = state['conmask']
    if mask != 0: # as per the C api
        _state['conmask'] = int(mask) # raises exception if mask isn't an int
    return omask

def _syslog4(state, facility, priority, msg):
    global _info, _sock

    hostname = '-' if state['hostname'] == '' else str(state['hostname'])
    ident = '-' if state['ident'] == '' else (str(state['ident']).replace(' ','_')+':') # EXTENSION: that .replace()
    facility = int(state['facility']) if facility == 0 else int(facility)
    option = int(state['option'])
    logmask = int(state['logmask'])
    conmask = int(state['conmask'])
    stderr = state['stderr']

    if priority & logmask:
        return

    # EXTENSION: automatically send LOG_CONSOLE and some priorities to console
    if facility == LOG_CONSOLE or (priority & conmask == 0) or (option & LOG_PERROR and stderr is None):
        print(_priorityprefixes[priority] + msg)

    if option & LOG_PERROR and stderr is not None:
        stderr.write(_priorityprefixes[priority] + msg + "\n") # caller must handle any exceptions

    if facility == LOG_CONSOLE or _address is False:
        return

    if _info is None:
        try:
            # EXTENSION: tuple not required, can just use a string and assume the port number
            if isinstance(_address, str) or isinstance(_address, bytes):
                _info = usocket.getaddrinfo(_address, SYSLOG_UDP_PORT)[0][-1]
            else:
                _info = usocket.getaddrinfo(_address[0], _address[1])[0][-1]
        except:
            if option & LOG_CONS:
                print(_priorityprefixes[LOG_CRIT] + "syslog: Exception in getaddrinfo(%s)" % (repr(_address),))
            return

    if _sock is None:
        try:
            _sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
        except:
            if option & LOG_CONS:
                print(_priorityprefixes[LOG_CRIT] + "syslog: Exception in socket(AF_INET,SOCK_DGRAM)")
            return

    # FEATURE: no unicode (or timestamps)
    data = ("<%d>1 %s %s %s - - - %s" % (facility|priority, _timestamp(), hostname, ident, msg)).encode()

    try:
        _sock.sendto(data, _info)
    except:
        if option & LOG_CONS:
            print(_priorityprefixes[LOG_CRIT] + "syslog: Exception in sendto()")
        # throw away the socket and get a new one next time
        try: _sock.close()
        except: pass
        _sock = None

# FEATURE: pri is optional in CPython, but required here
def syslog(pri, msg):
    _syslog4(_state, (pri & ~0x07), (pri &  0x07), msg)

#### Implement (part of) the SysLogHandler API ...

class Handler():

    # FEATURE: constructor doesn't take a socktype argument, and we only support UDP network traffic
    # EXTENSION: can set the ident per Handler() as well as the facility
    def __init__(self, address=None, facility=None, **kwargs):
        self._state = _state.copy()
        _update_state(self._state, address=address, facility=facility)
        _update_state(self._state, **kwargs)

    def close(self):
        closelog()

    def _log(self, priority, msg, *args):
        if args:
            msg = msg % args
        _syslog4(self._state, 0, priority, msg)

    def critical(self, msg, *args):
        self._log(LOG_CRIT, msg, *args)

    def error(self, msg, *args):
        self._log(LOG_ERR, msg, *args)

    def warning(self, msg, *args):
        self._log(LOG_WARNING, msg, *args)

    # EXTENSION: not in the SysLogHandler API (because NOTICE isn't a default LogHandler level)
    def notice(self, msg, *args):
        self._log(LOG_NOTICE, msg, *args)

    def info(self, msg, *args):
        self._log(LOG_INFO, msg, *args)

    def debug(self, msg, *args):
        self._log(LOG_DEBUG, msg, *args)



"""
# Not happy with this yet

    def _log(self, priority, msg, *args, exc_info=False):
        if args:
            msg = msg % args
        _syslog4(self._state, 0, priority, msg)

        if not exc_info:
            return

        import sys
        if isinstance(exc_info, BaseException):
            exc = exc_info
        elif isinstance(exc_info, tuple):
            exc = exc_info[1]
        else:
            try: exc = sys.exc_info()[1]
            except: return

        try: sys.print_exception(exc, _stderr) #TODO: shouldn't just be print()ed
        except: pass

    EXCEPTION_PRIORITY = LOG_ERR

    def exception(self, msg, *args, exc_info=True):
        self._log(self.EXCEPTION_PRIORITY, msg, *args, exc_info=exc_info)
"""
