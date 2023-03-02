from machine import Pin, SoftUART

class _PassLogHandler:
    def debug(self, msg):
        pass
    def info(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        pass
    def critical(self, msg):
        pass

class _PrintLogHandler:
    def debug(self, msg):
        print(msg)
    def info(self, msg):
        print(msg)
    def warning(self, msg):
        print(msg)
    def error(self, msg):
        print(msg)
    def critical(self, msg):
        print(msg)

class PM1006:
    def __init__(self, rxpin, **kwargs):
        log = kwargs.get('loghandler', None)
        if log is None or log is False:
            self._log = _PassLogHandler()
        elif log is True:
            self._log = _PrintLogHandler()
        else:
            self._log = log

        # tx is required but not used, doesn't even need to be connected
        # timeout must be over 2 seconds to capture a full Vindriktning cycle with one read()
        # but the smaller the timeout the better to maximise time left for non-UART stuff
        # this variable must start with double underscore because of a Thonny bug
        self._uart = SoftUART(baudrate=9600, rx=Pin(rxpin), tx=Pin(0), timeout=3000)

    def read_raw(self):
        self._log.debug('Waiting for UART')
        noreadcounter = 0
        while True: #TODO: timer argument to break out of this
            try:
                data = self._uart.read()
            except Exception as e:
                self._log.critical('Exception %s:%s while reading UART' % (type(e).__name__, e.args))
                return None
            if data is None or len(data) < 20:
                noreadcounter += 1
                if noreadcounter >= 20:
                    self._log.error('UART reading failed')
                    return None
                continue
            break
        self._log.debug('Read from UART (%d bytes)' % (len(data),))

        raw = []
        for offset in range(0,len(data),20):
            if offset+20 > len(data):
                self._log.warning('Partial frame at %d, ignoring reading' % (offset,))
                break
            if data[offset+0] != 22 or data[offset+1] != 17 or data[offset+2] != 11:
                # probably missed a symbol; in theory, we could resync on magic
                self._log.warning('Bad magic at %d, ignoring reading' % (offset,))
                continue # or break?
            if sum(data[offset:offset+20]) % 256 != 0:
                self._log.warning('Bad checksum at %d, ignoring reading' % (offset,))
                continue
            df3 = data[offset+5]
            df4 = data[offset+6]
            df3df4 = df3 * 256 + df4
            raw.append(df3df4)

        self._log.debug('UART values are %s' % (repr(raw),))

        return raw


    def read_one(self):

        raw = self.read_raw()

        if raw is None:
            # we already logged something
            return None

        if len(raw) < 1:
            self._log.error('UART values not found')
            return None

        raw.sort()

        one = raw[len(raw)//2] # median

        self._log.debug('UART value is %s' % (repr(raw),))

        return one


    def read(self):
        return self.read_one()
