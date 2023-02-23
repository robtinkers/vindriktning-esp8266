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
    _adjust_add = None
    _adjust_mul = None
    _smoothing = None

    def __init__(self, rxpin):
        self.set_logger(None)

        # tx is required but not used, doesn't even need to be connected
        # timeout must be over 2 seconds to capture a full Vindriktning cycle with one read()
        # but the smaller the timeout the better to maximise time left for non-UART stuff
        # this variable must start with double underscore because of a Thonny bug
        self._uart = SoftUART(baudrate=9600, rx=Pin(rxpin), tx=Pin(0), timeout=3000)

    def set_logger(self, logger):
        if logger is None or logger is False:
            self._logger = _PassLogHandler()
        elif logger is True:
            self._logger = _PrintLogHandler()
        else:
            self._logger = logger

    def set_adjust(self, add, mul):
        self._adjust_add = add
        self._adjust_mul = mul

    def set_smooth(self, smoothing):
        self._smoothing = smoothing



    def read_raw(self):
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

        raw = []
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
            raw.append(df3*256+df4)

        self._logger.info('UART readings are %s' % (repr(raw),))

        return raw


    # read_raw() returns an array of values
    #
    # read_one() converts that array into one value (e.g. median)
    #
    # read_adjusted() scales that one value linearly (typically a no-op)
    #
    # read_filtered() does some filter on the adjusted value (e.g. local minimum)
    #
    # read_smoothed() does exponential smoothing on the filtered value
    #
    # Note that read_adjusted also keeps a ring buffer of the latest values
    # This will be used in a traffic light system at some point in the future


    def read_one(self):

        raw = self.read_raw()

        if raw is None:
            # we already logged something
            return None

        if len(raw) < 1:
            self._logger.critical('UART readings not found')
            return None

        if len(raw) > 6:
            raw = raw[-6:]
        raw.sort()

        if raw[-1] >= raw[0] + 100:
            self._logger.warning('UART readings too volatile')
            return None

        if (len(raw) >= 6):
            one = float(raw[2] + raw[3]) / 2
        elif (len(raw) == 5):
            one = float(raw[2])
        elif (len(raw) == 4):
            one = float(raw[1] + raw[2]) / 2
        elif (len(raw) == 3):
            one = float(raw[1])
        else:
            self._logger.error('UART readings missing')
            return None

        return one



    _ringbuf = []
    _ringidx = None

    def read_adjusted(self):
        one = self.read_one()
        if one is None:
            # we already logged something
            return None

        adjusted = one
        if self._adjust_add is not None:
            adjusted = adjusted + self._adjust_add
        if self._adjust_mul is not None:
            adjusted = adjusted * self._adjust_mul
        if adjusted < 0:
            adjusted = 0.0

        # keep a ring buffer of the latest 120 values (adjusted but not filtered/smoothed)
        # this is ~60 minutes worth, assuming no read errors
        # TODO: is the read error rate high enough that it's worth tracking timestamps?
        if len(self._ringbuf) < 120:
            self._ringbuf.append(adjusted)
        else:
            if self._ringidx is None:
                self._ringidx = 0
            else:
                self._ringidx = (self._ringidx+1) % 120
            self._ringbuf[self._ringidx] = adjusted

        return adjusted



    _old_adjusted = None

    def read_filtered(self):
        oldvalue = self._old_adjusted
        adjusted = self.read_adjusted()
        self._old_adjusted = adjusted

        if adjusted is None or oldvalue is None:
            return adjusted

        return min(oldvalue, adjusted)



    _old_filtered = None

    def read_smoothed(self):
        oldvalue = self._old_filtered
        filtered = self.read_filtered()
        self._old_filtered = filtered

        if filtered is None or oldvalue is None:
            return filtered

        if self._smoothing is None:
            return filtered

        return (oldvalue * self._smoothing) + (filtered * (1 - self._smoothing))



    def read(self):
        return self.read_smoothed()
