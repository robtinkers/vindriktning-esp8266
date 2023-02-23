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
# import gc ; gc.collect() ; gc.mem_alloc() ; import usyslog ; z=usyslog.SyslogClient() ; gc.collect() ; gc.mem_alloc()
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
LOG_CONS = const(0x02) # "log on the console if errors in sending" [you probably want this]

#### more constants

# useful, also part of the SysLogHandler API
SYSLOG_UDP_PORT = const(514)

################

class SyslogClient:

    # FEATURE: constructor doesn't take a socktype argument, and we only support UDP network traffic
    def __init__(self, address=None, facility=None): # , socktype=usocket.SOCK_DGRAM):
#        assert socktype == usocket.SOCK_DGRAM
        self._address = address
        self._sock = None
        self._info = None
        self._stderr = None
        self.openlog(None, None, facility, None)

    # EXTENSION: redirect "stderr" so that LOG_PERROR logs to a file instead of the console
    # (actually, anything with a write method will work)
    def perror(self, fh):
        self._stderr = fh

    #### Implement (most of) the syslog wrapper API ...

    def closelog(self):
        try: self._sock.close()
        except: pass
        self._sock = None
        self._info = None
        self.openlog()

    # EXTENSION: hostname can be set by caller
    def openlog(self, ident=None, logoption=None, facility=None, hostname=None):
        self._ident = '-' if (ident is None or ident == '') else (str(ident)+':').replace(' ','_') # EXTENSION: that .replace()
        self._option = 0 if logoption is None else int(logoption)
        self._facility = LOG_USER if facility is None else int(facility)
        self._hostname = '-' if (hostname is None or hostname == '') else str(hostname)

    # added at the start of lines printed on the console (or written to a file if using perror redirection)
    # if you want to change this, then monkey patch or sub-class (leading underscore because interface may change)
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

    logmask = 0 # note that the mask configures what is *ignored*
## save a little memory by not implementing this API call, access the value directly if needed
#    def setlogmask(self, mask):
#        omask = self.logmask
#        if mask != 0: # as per the C api
#            self.logmask = mask
#        return omask

    # EXTENSION: automatically send some priorities to console
    conmask = ~(LOG_EMERG|LOG_ALERT)
## save a little memory by not implementing this API call, access the value directly if needed
#    def setconmask(self, mask):
#        omask = self.conmask
#        if mask != 0:
#            self.conmask = mask
#        return omask

    # FEATURE: pri is optional in CPython, but required here
    def syslog(self, pri, msg):
        facility = (pri & ~0x07)
        priority = (pri &  0x07)
        if facility == 0:
            facility = self._facility

        if priority & self.logmask:
            return

        if facility == LOG_CONSOLE or (priority & self.conmask == 0) or (self._option & LOG_PERROR and self._stderr is None):
            print(self._priorityprefixes[priority] + msg)

        if self._option & LOG_PERROR and self._stderr is not None:
            self._stderr.write(self._priorityprefixes[priority] + msg + "\n") # caller must handle any exceptions

        if facility == LOG_CONSOLE or self._address is None:
            return

        if self._info is None:
            try:
                # EXTENSION: tuple not required, can just use a string and assume the port number
                if isinstance(self._address, str) or isinstance(self._address, bytes):
                    self._info = usocket.getaddrinfo(self._address, SYSLOG_UDP_PORT)[0][-1]
                else:
                    self._info = usocket.getaddrinfo(self._address[0], self._address[1])[0][-1]
            except:
                if self._option & LOG_CONS:
                    print(self._priorityprefixes[LOG_CRIT] + "syslog: Exception in getaddrinfo()")
                return

        if self._sock is None:
            try:
                self._sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
            except Exception as e:
                if self._option & LOG_CONS:
                    print(self._priorityprefixes[LOG_CRIT] + "syslog: Exception in socket()")
                return

        # FEATURE: no timestamps or unicode
        data = ("<%d>1 - %s %s - - - %s" % (facility|priority, self._hostname, self._ident, msg)).encode()

        try:
            self._sock.sendto(data, self._info)
        except:
            if self._option & LOG_CONS:
                print(self._priorityprefixes[LOG_CRIT] + "syslog: Exception in sendto()")
            # throw away the socket and get a new one next time
            try: self._sock.close()
            except: pass
            self._sock = None

    #### Implement (part of) the SysLogHander API ...

    def close(self):
        self.closelog()

    def critical(self, msg):
        self.syslog(LOG_CRIT, msg)

    def error(self, msg):
        self.syslog(LOG_ERR, msg)

    def warning(self, msg):
        self.syslog(LOG_WARNING, msg)

    # EXTENSION: not in the SysLogHandler API (because NOTICE isn't a default LogHandler level)
    def notice(self, msg):
        self.syslog(LOG_NOTICE, msg)

    def info(self, msg):
        self.syslog(LOG_INFO, msg)

    def debug(self, msg):
        self.syslog(LOG_DEBUG, msg)

    # FEATURE: LOG_ALERT and LOG_EMERG don't get convenience functions
