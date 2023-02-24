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
    _how_broken = None
    _adjust_mul = None
    _adjust_add = None
    _exp_smooth = None

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

    def set_broken(self, broken):
        self._how_broken = broken

    def set_adjust(self, mul, add):
        self._adjust_mul = mul
        self._adjust_add = add

    def set_smooth(self, smooth):
        self._exp_smooth = smooth


    # read_raw() returns an array of values
    #
    # read_one() converts that array into one value (e.g. median)
    #
    # read_adjusted() scales that one value linearly (configured by caller, default is no-op)
    #
    # read_filtered() does some filter on the adjusted value (e.g. local minimum)
    #
    # read_smoothed() does exponential smoothing on the filtered value (configured by caller, default is no-op)
    #
    # Note that read_adjusted() also keeps a ring buffer of the latest values (i.e. adjusted, not filtered or smoothed)
    # This will be used in a traffic light system at some point in the future


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
                if noreadcounter >= 20:
                    self._logger.error('UART readings failed')
                    return None
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

        self._logger.debug('UART readings are %s' % (repr(raw),))

        return raw



    def read_one(self):

        raw = self.read_raw()

        if raw is None:
            # we already logged something
            return None

        if len(raw) < 1:
            self._logger.error('UART readings not found')
            return None

        if len(raw) > 6:
            raw = raw[-6:]
        raw.sort()

        if raw[-1] >= raw[0] + 100:
            self._logger.warning('UART readings too volatile')
            return None

        broken = 0
        if self._how_broken is not None:
            while len(raw) and raw[0] < self._how_broken:
                broken += 1
                raw.pop(0)

        if len(raw) < 3:
            if broken:
                self._logger.error('UART readings are broken')
            else:
                self._logger.error('UART readings are missing')
            return None
        elif (len(raw) == 3):
            return float(raw[1])
        elif (len(raw) == 4):
            return float(raw[1] + raw[2]) / 2
        elif (len(raw) == 5):
            return float(raw[2])
        else:
            return float(raw[2] + raw[3]) / 2



    _adjbuf = []
    _adjidx = None

    def read_adjusted(self):
        adjusted = self.read_one()

        if adjusted is not None:

            if self._adjust_mul is not None:
                adjusted *= self._adjust_mul
            if self._adjust_add is not None:
                adjusted += self._adjust_add
            if adjusted < 0:
                adjusted = 0.0

            # keep a ring buffer of the latest 120 values (adjusted but not filtered/smoothed)

            if len(self._adjbuf) < 120:
                self._logger.debug('Adding latest value to ring buffer (%d)' % (adjusted,))
                self._adjbuf.append(adjusted)
            else:
                if self._adjidx is None:
                    self._adjidx = 0
                else:
                    self._adjidx = (self._adjidx + 1) % 120
                self._logger.debug('Adding latest value to ring buffer %d' % (adjusted,))
                self._adjbuf[self._adjidx] = adjusted

        else: # there were read errors, so substitute in the last-known-good value

            if len(self._adjbuf) == 0:
                pass
            elif len(self._adjbuf) < 120:
                oldval = self._adjbuf[-1]
                self._logger.debug('Adding last-known-good value to ring buffer (%d)' % (oldval,))
                self._adjbuf.append(oldval)
            else:
                oldval = self._adjbuf[self._adjidx]
                self._logger.debug('Adding last-known-good value to ring buffer (%d)' % (oldval,))
                self._adjidx = (self._adjidx + 1) % 120
                self._adjbuf[self._adjidx] = oldval

        return adjusted



    _old_adjusted = None

    def read_filtered(self):
        oldvalue = self._old_adjusted
        adjusted = self.read_adjusted()
        self._old_adjusted = adjusted

        if adjusted is None or oldvalue is None:
            return adjusted

        return min(oldvalue, adjusted)



    _old_smoothed = None

    def read_smoothed(self):
        filtered = self.read_filtered()

        try: self._old_smoothed = (self._old_smoothed * self._exp_smooth) + (filtered * (1 - self._exp_smooth))
        except TypeError: self._old_smoothed = filtered # one of those variables was None

        return self._old_smoothed



    def read(self):
        return self.read_smoothed()
