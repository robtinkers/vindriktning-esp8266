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

class PM1006_Sensor:
    uart = None

    adjusted_ringbuf = []
    adjusted_ringidx = None
    last_adjusted = None
    last_smoothed = None

    def __init__(self, rxpin, **kwargs):
        logger = kwargs.get('logger', None)
        if logger is None:
            logger = False
        if logger is False:
            self.logger = _PassLogHandler()
        elif logger is True:
            self.logger = _PrintLogHandler()
        else:
            self.logger = logger
        # tx is required but not used, doesn't even need to be connected
        # timeout must be over 2 seconds to capture a full Vindriktning cycle with one read()
        # but the smaller the timeout the better to maximise time left for non-UART stuff
        # this variable must start with double underscore because of a Thonny bug
        self.uart = SoftUART(baudrate=9600, rx=Pin(rxpin), tx=Pin(0), timeout=3000)

    def read_uart(self, **kwargs):
        verbose = kwargs.get('verbose', False)

        self.logger.debug('Waiting for UART')
        while True:
            try:
                if verbose:
                    print('.', end='')
                data = self.uart.read()
            except Exception as e:
                if verbose:
                    print()
                self.logger.critical('Exception %s:%s while reading UART' % (type(e).__name__, e.args))
                return None
            if data is None:
                continue
            break
        if verbose:
            print()
        self.logger.debug('Read from UART (%d bytes)' % (len(data),))

        readings = []
        for offset in range(0,len(data),20):
            if offset+20 > len(data):
                self.logger.warning('Partial frame at %d, ignoring reading' % (offset,))
                break
            if data[offset+0] != 22 or data[offset+1] != 17 or data[offset+2] != 11:
                # TODO: probably missed a symbol; in theory, we could resync on magic
                self.logger.warning('Bad magic at %d, ignoring reading' % (offset,))
                continue # or break?
            if sum(data[offset:offset+20]) % 256 != 0:
                self.logger.warning('Bad checksum at %d, ignoring reading' % (offset,))
                continue
            df3 = data[offset+5]
            df4 = data[offset+6]
            readings.append(df3*256+df4)

        self.logger.info('UART readings are %s' % (repr(readings),))

        return readings



    def read(self, smoothing=None, add=None, mul=None):

        readings = self.read_uart(verbose=True)

        if readings is None:
            # we already logged something
            self.last_adjusted = None
            self.last_smoothed = None
            return None

        if len(readings) < 1:
            self.logger.critical('UART readings not found')
            self.last_adjusted = None
            self.last_smoothed = None
            return None

        # the sensor readings can be very spiky, so we do a lot of smoothing...

        # 1.
        if len(readings) > 6:
            readings = readings[-6:]
        readings.sort()

    #    # 2.
    #    if readings[-1] == 0:
    #        self.logger.warning('UART readings all zero')
    #        self.last_adjusted = None
    #        self.last_smoothed = None
    #        return None

        # 3.
        if readings[-1] >= readings[0] + 100:
            self.logger.warning('UART readings too volatile')
            self.last_adjusted = None
            self.last_smoothed = None
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
            self.logger.error('UART readings missing')
            self.last_adjusted = None
            self.last_smoothed = None
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
        if len(self.adjusted_ringbuf) < 120:
            self.adjusted_ringbuf.append(pm1006)
        else:
            if self.adjusted_ringidx is None:
                self.adjusted_ringidx = 0
            else:
                self.adjusted_ringidx = (self.adjusted_ringidx+1) % 120
            self.adjusted_ringbuf[self.adjusted_ringidx] = pm1006

        # 5.
        if self.last_adjusted is not None and pm1006 > self.last_adjusted:
            (pm1006, self.last_adjusted) = (self.last_adjusted, pm1006)
        else:
            self.last_adjusted = pm1006

        # 5.
        if self.last_smoothed is not None and smoothing is not None:
            pm1006 = (self.last_smoothed * smoothing) + (pm1006 * (1 - smoothing))
        self.last_smoothed = pm1006

        return pm1006
