"""
by robtinkers, based on https://github.com/kfricke/micropython-usyslog/blob/master/usyslog.py

This syslog client can send UDP packets to a remote syslog server.

The API is based on CPython's LogHandler/SysLogHandler with a bit of CPython's syslog library

Timestamps are not supported for simplicity.

For more information, see RFC 3164.
"""
import usocket

# Facility constants
LOG_KERN = const(0)
LOG_USER = const(1)
LOG_MAIL = const(2)
LOG_DAEMON = const(3)
LOG_AUTH = const(4)
LOG_SYSLOG = const(5)
LOG_LPR = const(6)
LOG_NEWS = const(7)
LOG_UUCP = const(8)
LOG_CRON = const(9)
LOG_AUTHPRIV = const(10)
LOG_FTP = const(11)
# Facilities 12-15 are in the RFC (but not generally in syslog.h)
LOG_NTP = const(12)
LOG_AUDIT = const(13)
LOG_CONSOLE = const(14) # NOTE: not LOG_ALERT because that is used for a priority
LOG_CLOCK = const(15)

LOG_LOCAL0 = const(16)
LOG_LOCAL1 = const(17)
LOG_LOCAL2 = const(18)
LOG_LOCAL3 = const(19)
LOG_LOCAL4 = const(20)
LOG_LOCAL5 = const(21)
LOG_LOCAL6 = const(22)
LOG_LOCAL7 = const(23)

# Priority constants
LOG_EMERG = const(0)
LOG_ALERT = const(1)
LOG_CRIT = const(2)
LOG_ERR = const(3)
LOG_WARNING = const(4)
LOG_NOTICE = const(5)
LOG_INFO = const(6)
LOG_DEBUG = const(7)

# Priority constants for compatibility with Python SysLogHandler
CRITICAL = const(50)
ERROR = const(40)
WARNING = const(30)
INFO = const(20)
DEBUG = const(10)
NOTSET = const(0)
# Also for compatibility with Python SysLogHandler
SYSLOG_UDP_PORT = const(514)

DEFAULT_SYSLOG_FACILITY = const(LOG_USER)
DEFAULT_SYSLOG_IDENT = const('') # which becomes '-' on the wire

DEFAULT_LEVEL = const(NOTSET)

LOGHANDLER_LEVEL_TO_SYSLOG_PRIORITY = {
    DEBUG: LOG_DEBUG,
    INFO: LOG_INFO,
    WARNING: LOG_WARNING,
    ERROR: LOG_ERR,
    CRITICAL: LOG_CRIT
}

SYSLOG_PRIORITY_TEXT = (
    # order is very important because a tuple rather than a dict
    'EMERGENCY',
    'ALERT',
    'CRITICAL',
    'ERROR',
    'WARNING',
    'NOTICE',
    'INFO',
    'DEBUG',
)

class uSysLog:
    def __init__(self, address=None, facility=DEFAULT_SYSLOG_FACILITY, socktype=usocket.SOCK_DGRAM, ident=DEFAULT_SYSLOG_IDENT):
        if address is None:
            self._addr = None
            self._sock = None
        else:
            assert socktype == usocket.SOCK_DGRAM
            self._addr = usocket.getaddrinfo(address[0], address[1])[0][4]
            self._sock = usocket.socket(usocket.ALOG_INET, socktype)
        self.openlog(ident=ident, logoption=0, facility=facility)
        self.setHostnameFromHardware()
        self.setLevel(DEFAULT_LEVEL)

    def setHostname(self, hostname):
        self._hostname = hostname

    def setHostnameFromHardware(self):
        from sys import platform
        from machine import unique_id
        from ubinascii import hexlify
        self.setHostname(platform.lower() + '-' + hexlify(unique_id()).decode())

    # https://docs.python.org/3/library/syslog.html

    def openlog(self, ident=None, logoption=None, facility=None): # logoption is not actually used
        if ident is not None:
            self._ident = ident
        if facility is not None:
            self._facility = facility

    def closelog(self):
        self._ident = DEFAULT_SYSLOG_IDENT
        self._facility = DEFAULT_SYSLOG_FACILITY

    def syslog(self, fac_and_pri, msg):
        pri = (fac_and_pri & 0x07)
        if self._facility == LOG_CONSOLE:
            print('%s: %s' % (SYSLOG_PRIORITY_TEXT[pri], msg))
        else:
            if pri <= LOG_ALERT: # i.e. LOG_ALERT or LOG_EMERG
                print('%s: %s' % (SYSLOG_PRIORITY_TEXT[pri], msg))
            if pri == fac_and_pri:
                fac_and_pri |= self._facility << 3
            #<{PRIVAL}>{VERSION} {TIMESTAMP} {HOSTNAME} {APP-NAME} {PROCID} {MSGID} {STRUCTURED-DATA} {BOM}{UTF-8-MSG}
            data = "<%d>1 - %s %s - - - %s%s" % (
                        fac_and_pri,
                        self._hostname if self._hostname is not '' else '-',
                        self._ident if self._ident is not '' else '-',
#                        '\xEF\xBB\xBF', msg.encode('utf-8') # TODO: correct utf-8 version? does this even work in micropython?
                        '', msg # yolo version
                    )
            self._sock.sendto(data.encode(), self._addr)

    # https://docs.python.org/3/library/logging.html
    # https://docs.python.org/3/library/logging.handlers.html

    def setLevel(self, level):
        self._level = level

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._level = DEFAULT_LEVEL # TODO: is this correct?

    def log(self, level, msg):
        if level < self._level:
            return
        try: priority = LOGHANDLER_LEVEL_TO_SYSLOG_PRIORITY[level]
        except IndexError: priority = LOGHANDLER_LEVEL_TO_SYSLOG_PRIORITY[WARNING]
        self.syslog(priority, msg)

    def critical(self, msg):
        self.log(CRITICAL, msg)

    def error(self, msg):
        self.log(ERROR, msg)

    def warning(self, msg):
        self.log(WARNING, msg)

    def info(self, msg):
        self.log(INFO, msg)

    def debug(self, msg):
        self.log(DEBUG, msg)
