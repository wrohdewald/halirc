#!/usr/bin/env python
# -*- coding: utf-8 -*-

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Copyright (C) 2011 Wolfgang Rohdewald <wolfgang@rohdewald.de>

halirc is free software you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""

import datetime, socket, subprocess
import logging, logging.handlers
import serial
from optparse import OptionParser


LOGGER = None
OPTIONS = None

class Device(object):
    """if we ever support other than SerialDevices, we should
    move some methods here from SerialDevice"""
    def __init__(self, name):
        self.deviceName = name
        self.event = None # the event we are currently reacting on
        self.lastUsed = None
        self.lastOpenFailTime = None
        self.closeAfterUnusedSeconds = 5

    def setEvent(self, event):
        """event is the next event to be processed or None if there
        was no event while timeout"""
        self.event = event
        if not event and self.lastUsed:
            if elapsedSince(self.lastUsed) > self.closeAfterUnusedSeconds:
                self.close()

    def open(self):
        """open device. Please override. Return False if open fails"""
        if self.lastOpenFailTime and elapsedSince(self.lastOpenFailTime) < 10:
            # if the previous open failed, wait for a moment until retry
            return False
        self.lastOpenFailTime = None # after timeout, forget cached error
        if 's' in OPTIONS.debug or 'r' in OPTIONS.debug:
            LOGGER.debug('opening %s' % type(self).__name__)
        self.lastUsed = datetime.datetime.now()
        return True

    def close(self, openFailed=False):
        """reset closing timer. Please override."""
        if openFailed:
            self.lastOpenFailTime = datetime.datetime.now()
        else:
            self.lastOpenFailTime = None
        self.lastUsed = None
        if 's' in OPTIONS.debug or 'r' in OPTIONS.debug:
            LOGGER.debug('closing %s' % type(self).__name__)

class SerialDevice(Device):
    """for RS232 connections"""
    # pylint: disable=R0913
    # too many arguments
    def __init__(self, name, baud=9600, bits=8, parity=serial.PARITY_NONE,
              stop=serial.STOPBITS_ONE, timeout=0.01, xonxoff=0, rtscts=0):
        Device.__init__(self, name)
        self.baud = baud
        self.bits = bits
        self.parity = parity
        self.stop = stop
        self.timeout = timeout
        self.xonxoff = xonxoff
        self.rtscts = rtscts
        self.serio = None
        # the usb/serial needs 1/4 second for open!
        self.closeAfterUnusedSeconds = 10

    def init(self):
        """initialize device for normal usage"""
        pass

    def isPoweredOn(self):
        """is the device powered on?"""
        pass

    # pylint: disable=R0201
    # this could be a function but not the descendants
    def delay(self, dummyCommand=None, dummyParameters=None):
        """how long should we wait for answer from device?"""
        return 10

    def readline(self, eol):
        """only call this when we know the device is
        ready for sending"""
        if not self.open():
            return ''
        result = ''
        waited = 0
        while True:
            char = self.serio.read(1)
            waited = waited + self.serio.timeout
            if char == '':
                if waited >= self.delay():
                    break
                else:
                    continue
            if char == eol:
                break
            result += char
        if result and 'r' in OPTIONS.debug:
            LOGGER.debug('from %s: %s' % (type(self).__name__, result))
        return result

    def communicate(self, data, getAnswer=False, eol='\r'):
        """send data to device, set lastUsed, optionally get answer from device"""
        if not self.open():
            return ''
        if 's' in OPTIONS.debug:
            LOGGER.debug('sending to %s: %s' % (type(self).__name__, data))
        self.serio.write(data)
        self.lastUsed = datetime.datetime.now()
        if getAnswer:
            result = self.readline(eol)
        else:
            result = None
        return result

    def open(self):
        """if the device is not connected, do so"""
        if not self.serio:
            if not Device.open(self):
                return False
            try:
                self.serio = serial.Serial(self.deviceName, baudrate=self.baud,
                  bytesize=self.bits, parity=self.parity,
                  stopbits=self.stop, timeout=self.timeout,
                  xonxoff=self.xonxoff, rtscts=self.rtscts)
            except Exception as exception: # pylint: disable=W0703
                LOGGER.error('cannot open %s: %s' % (type(self).__name__, exception))
                return False
        return True

    def close(self, openFailed=False):
        """only close when unused for some time, see caller"""
        if self.serio:
            self.serio.close()
            self.serio = None
        Device.close(self, openFailed)

class Event(object):
    """keeps attributes for events from lirc"""
    def __init__(self, line=None, name=None, remote=None, button=None, repeat='00'):
        if line:
            if 'i' in OPTIONS.debug:
                LOGGER.debug('event:%s ' % line)
            self.code, self.repeat, self.button, self.remote = \
                line.strip().split(' ')
            self.name = ''
        else:
            self.code = None
            self.name = name
            self.remote = remote
            self.button = button
            self.repeat = repeat

    def __str__(self):
        return '%s %s %s' % (
           (self.name or 'anon'),
           (self.remote or 'all remotes'),
           (self.button or 'all buttons'))

    def __eq__(self, other):
        """compares remote, button, repeat. None is a wildcard."""
        if self.remote and other.remote and self.remote != other.remote:
            return False
        if self.button and other.button and self.button != other.button:
            return False
        if self.repeat and other.repeat and self.repeat != other.repeat:
            return False
        return True

def elapsedSince(since):
    """return the seconds elapsed since 'since'"""
    if since is not None:
        x = datetime.datetime.now() - since
        return float(x.microseconds + (x.seconds + x.days * 24 * 3600) * 10**6) / 10**6

class Irw(object):
    """get lirc events from the lirc socket, just like irw does"""
    LF = '\n'
    def __init__(self, name=None):
        if not name:
            name = '/var/run/lirc/lircd'
        self.name = name
        self.input = socket.socket( socket.AF_UNIX, socket.SOCK_STREAM)
        self.input.connect(name)
        self.input.settimeout(1)
        self.buf = ''

    def read(self):
        """returns a complete line from the lirc socket
        or None if timeout is reached"""
        if not self.LF in self.buf:
            try:
                self.buf += self.input.recv(100)
            except socket.timeout:
                pass
        if self.LF in self.buf:
            lines = self.buf.split(self.LF)
            self.buf = self.LF.join(lines[1:])
            return Event(lines[0])

def parseOptions():
    """should switch to argparse when debian stable has python 2.7"""
    parser = OptionParser()
    parser.add_option('-d', '--debug', dest='debug',
        help="""DEBUG:
a sequence of characters: 's' shows data sent to appliances.
'r' shows data read from appliances.
'i' shows data received from remote controls.
'c' shows changes in the appliance status.
'f' shows filtering info
             """, default='', metavar='DEBUG')
    parser.add_option('-b', '--background', dest='background',
        action="store_true", default=False,
        help="run in background. Logging goes to the syslogs.")
    global OPTIONS # pylint: disable=W0603
    OPTIONS = parser.parse_args()[0]

def initLogger():
    """logging goes to stderr when running in foregrund, else
    to syslog"""
    global LOGGER # pylint: disable=W0603
    LOGGER = logging.getLogger('halirc')
    print 'initlog: LOGGER:', LOGGER
    if OPTIONS.background:
        handler = logging.handlers.SysLogHandler('/dev/log')
    else:
        handler = logging.StreamHandler()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.DEBUG)
    if OPTIONS.background:
        # if we generate a ton of same messages, give syslog a change
        # to reduce log file output by always writing exactly the same msg
        formatter = logging.Formatter("%(name)s: %(levelname)s %(message)s")
    else:
        formatter = logging.Formatter("%(relativeCreated)d %(name)s: %(levelname)s %(message)s")
    handler.setFormatter(formatter)

class Timer(object):
    """hold attributes needed for a timer"""
    # pylint: disable=R0913
    def __init__(self, action, name, args,
            minute=None, hour=None, day=None, month=None, weekday=None):
        self.action = action
        self.name = name
        self.args = args
        self.minute = minute
        self.hour = hour
        self.day = day
        self.month = month
        self.weekday = weekday
        self.lastDone = None

    def trigger(self):
        """if this timer should be executed now, do so"""
        now = datetime.datetime.now()
        if not self.lastDone or elapsedSince(self.lastDone) > 65:
            for tValue, nValue in (
                  (self.minute, now.minute),
                  (self.hour, now.hour),
                  (self.day, now.day),
                  (self.month, now.month),
                  (self.weekday, now.weekday())):
                if tValue is not None:
                    if isinstance(tValue, list):
                        if nValue not in tValue:
                            return
                    else:
                        if tValue != nValue:
                            return
            self.lastDone = now
            if self.args:
                if isinstance(self.args, list):
                    self.action(*self.args) # pylint: disable=W0142
                else:
                    self.action(self.args)
            else:
                self.action()

class Worker(object):
    """executes events if they match filters"""
    def __init__(self):
        self.filters = []
        self.events = []
        self.timers = []

    def addFilter(self, action, name=None, args=None, remote=None, button=None, repeat='00'):
        """None works as wildcard for remote, button, repeat.
        remote and button must always match literally.
        if more than one filters match, they are executed in their
        defined order. If a filter sets stopFiltering to True, the following filters
        will be ignored.
        args can be a single value or a list of arguments"""

        self.filters.append((Event(name=name, remote=remote, button=button, repeat=repeat),
             action, args))

    # pylint: disable=R0913
    def addTimer(self, action, args=None, name=None, minute=None, hour=None,
           day=None, month=None, weekday=None):
        """action is a method to be called with args
        when is a python datetime object"""
        self.timers.append(Timer(action, name, args, minute, hour, day, month, weekday))

    def execute(self, event):
        """for now, return True if any filter matched"""
        for timer in self.timers:
            timer.trigger()
        if event:
            foundFilter = False
            for fltr, action, args in self.filters:
                if fltr == event:
                    foundFilter = True
                    if 'f' in OPTIONS.debug:
                        LOGGER.debug('executing filter %s' % str(fltr))
                    if args:
                        if isinstance(args, list):
                            stopFiltering = action(*args) # pylint: disable=W0142
                        else:
                            stopFiltering = action(args)
                    else:
                        stopFiltering = action()
                    if stopFiltering is True:
                        break
            return foundFilter

def currentConsole():
    """returns the current fg console"""
    result = subprocess.Popen('fgconsole', stdout=subprocess.PIPE,
        stderr=subprocess.PIPE).communicate()
    if result[1]:
        LOGGER.error('fgconsole:%s' % result[1])
        return None
    return int(result[0][:-1])

