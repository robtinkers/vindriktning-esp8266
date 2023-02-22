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
    _uart = None
    _adjust_add = None
    _adjust_mul = None
    _smoothing = None
    
    adjusted_ringbuf = []
    adjusted_ringidx = None
    last_adjusted = None
    last_smoothed = None

    def __init__(self, rxpin, **kwargs):
        # handle 'logger' first so we can log startup
        logger = kwargs.get('logger', None)
        if logger is None:
            logger = False
        if logger is False:
            self._logger = _PassLogHandler()
        elif logger is True:
            self._logger = _PrintLogHandler()
        else:
            self._logger = logger

        # tx is required but not used, doesn't even need to be connected
        # timeout must be over 2 seconds to capture a full Vindriktning cycle with one read()
        # but the smaller the timeout the better to maximise time left for non-UART stuff
        # this variable must start with double underscore because of a Thonny bug
        self._uart = SoftUART(baudrate=9600, rx=Pin(rxpin), tx=Pin(0), timeout=3000)

        self._adjust_add = kwargs.get('add', None)
        self._adjust_mul = kwargs.get('mul', None)
        self._smoothing = kwargs.get('smoothing', None)

    def read_uart(self, **kwargs):
        self._logger.debug('Waiting for UART')
        noreadcounter = 0
        while True: # TODO: timer argument to break out of this
            try:
                data = self._uart.read()
            except Exception as e:
                self._logger.critical('Exception %s:%s while reading UART' % (type(e).__name__, e.args))
                return None
            if data is None or len(data) < 20:
                noreadcounter += 1
                if noreadcounter == 10:
                    self._logger.warning('uart.read() failed %d times' % (noreadcounter,))
                elif noreadcounter == 20:
                    self._logger.error('uart.read() failed %d times' % (noreadcounter,))
                elif noreadcounter >= 30 and (noreadcounter % 10) == 0:
                    self._logger.critical('uart.read() failed %d times' % (noreadcounter,))
                continue
            break
        self._logger.debug('Read from UART (%d bytes)' % (len(data),))

        readings = []
        for offset in range(0,len(data),20):
            if offset+20 > len(data):
                self._logger.warning('Partial frame at %d, ignoring reading' % (offset,))
                break
            if data[offset+0] != 22 or data[offset+1] != 17 or data[offset+2] != 11:
                # TODO: probably missed a symbol; in theory, we could resync on magic
                self._logger.warning('Bad magic at %d, ignoring reading' % (offset,))
                continue # or break?
            if sum(data[offset:offset+20]) % 256 != 0:
                self._logger.warning('Bad checksum at %d, ignoring reading' % (offset,))
                continue
            df3 = data[offset+5]
            df4 = data[offset+6]
            readings.append(df3*256+df4)

        self._logger.info('UART readings are %s' % (repr(readings),))

        return readings



    def read(self):

        readings = self.read_uart()

        if readings is None:
            # we already logged something
            self.last_adjusted = None
            self.last_smoothed = None
            return None

        if len(readings) < 1:
            self._logger.critical('UART readings not found')
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
    #        self._logger.warning('UART readings all zero')
    #        self.last_adjusted = None
    #        self.last_smoothed = None
    #        return None

        # 3.
        if readings[-1] >= readings[0] + 100:
            self._logger.warning('UART readings too volatile')
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
            self._logger.error('UART readings missing')
            self.last_adjusted = None
            self.last_smoothed = None
            return None

        #
        if self._adjust_add is not None:
            pm1006 = pm1006 + self._adjust_add
        if self._adjust_mul is not None:
            pm1006 = pm1006 * self._adjust_mul
        if pm1006 < 0:
            pm1006 = 0.0

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
        if self.last_smoothed is not None and self._smoothing is not None:
            pm1006 = (self.last_smoothed * self._smoothing) + (pm1006 * (1 - self._smoothing))
        self.last_smoothed = pm1006

        return pm1006
