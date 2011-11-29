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

import datetime, daemon, os
import logging, logging.handlers
from optparse import OptionParser

from twisted.protocols.basic import LineOnlyReceiver
from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory
from twisted.internet.protocol import ProcessProtocol
from twisted.internet.defer import Deferred, succeed

# this ugly code ensures that pylint gives no errors about
# undefined attributes:
reactor.callLater = reactor.callLater
reactor.run = reactor.run
reactor.connectUNIX = reactor.connectUNIX
reactor.spawnProcess = reactor.spawnProcess

LOGGER = None
OPTIONS = None

def elapsedSince(since):
    """return the seconds elapsed since 'since'"""
    if since is not None:
        x = datetime.datetime.now() - since
        return float(x.microseconds + (x.seconds + x.days * 24 * 3600) * 10**6) / 10**6

class IrwProtocol(LineOnlyReceiver):
    """protocol for reading the lirc socket"""
    # pylint: disable=W0232

    delimiter = '\n'

    def lineReceived(self, data):
        """we got a raw line from the lirc socket"""
        code, repeat, button, remote = data.strip().split(' ')
        # pylint: disable=E1101
        self.factory.hal.eventReceived(RemoteEvent(remote, button, repeat, code))

class IrwFactory(ClientFactory):
    """factory for the lirc socket"""
    protocol = IrwProtocol

    def __init__(self, hal):
        self.hal = hal

def parseOptions():
    """should switch to argparse when debian stable has python 2.7"""
    parser = OptionParser()
    parser.add_option('-d', '--debug', dest='debug',
        help="""DEBUG:
a sequence of characters: 's' shows data sent to appliances.
'r' shows data read from appliances.
'e' shows events received
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
    return LOGGER

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

class Event(object):
    """for all events coming from outside. Also used for
    filter definitions."""
    def __init__(self, sender, **kwargs):
        self.when = datetime.datetime.now()
        self.sender = sender
        for key, value in kwargs.items():
            self.__setattr__(key, value)
    def __str__(self):
        return str(self.__dict__)

    def __eq__(self, other):
        if type(self) != type(other):
            return False
        keys = set(self.__dict__.keys()) | set(other.__dict__.keys())
        for key in keys:
            if key != 'when':
                myValue = getattr(self, key)
                otherValue = getattr(other, key)
                if myValue is not None and otherValue is not None and myValue != otherValue:
                    return False
        return True

class RemoteEvent(Event):
    """event from a remote control"""
    def __init__(self, remote=None, button=None, repeat='00', code=None):
        Event.__init__(self, 'remote%s' % remote, button=button, repeat=repeat, code=code)

    def __str__(self):
        """for debugging messages"""
        remote = self.sender[6:]
        # pylint: disable=E1101
        # pylint does not know button/repeat exist, but we do
        return ('event from remote control: %s.%s %s' % (
           (remote or '*'),
           (self.button or '*'),
           (self.repeat or ''))).strip()

class MessageEvent(Event):
    """event with a message"""
    def __init__(self, message):
        self.message = None
        Event.__init__(self, 'Denon', message=message)

    def __str__(self):
        return 'event from %s: %s' % (self.sender, str(self.message))

    def __eq__(self, other):
        """only compare humanCommand, not the value"""
        if type(self) != type(other):
            return False
        return self.message.humanCommand() == other.message.humanCommand()

class Message(object):
    """holds content of a message from or to a device"""
    def __init__(self, decoded=None, encoded=None):
        assert (decoded is None) != (encoded is None), \
            'decoded:%s encoded:%s' % (decoded, encoded)
        if decoded is not None:
            assert isinstance(decoded, basestring)
        if encoded is not None:
            assert isinstance(encoded, basestring)
        self._encoded = None
        self._decoded = None
        self.ask = False
        self._setAttributes(decoded, encoded)
        self.status = 'OK' # the status returned from device: 'OK' or an error string

    @apply
    def encoded(): # pylint: disable=E0202
        """get message string in transport format"""
        def fget(self):
            # pylint: disable=W0212
            return self._encoded
        return property(**locals())

    @apply
    def decoded(): # pylint: disable=E0202
        """get human readable message string"""
        def fget(self):
            # pylint: disable=W0212
            return self._decoded
        return property(**locals())

    def _setAttributes(self, decoded, encoded):
        """initialise decoded / encoded. Should be overridden"""
        self._decoded = self._encoded = decoded or encoded

    def humanCommand(self):
        """the human readable command"""
        return self.command()

    def humanValue(self):
        """the human readable value"""
        return self.value()

    def __str__(self):
        """use the human readable form for logging"""
        result = self.humanCommand()
        if not result:
            return 'None'
        if self.humanValue():
            result += ':%s' % self.humanValue()
        else:
            result += '?'
        return result

    def __eq__(self, other):
        """are they identical?"""
        return self.decoded == other.decoded

class Filter(object):
    """a filter always has a name. events is a single event or a list of events.
       maxTime is of type timedelta, with default = len(events) seconds.
       if stopIfMatch=True and this Filter matches, do not look at following filters"""
    def __init__(self, events, action, name=None, args=None, maxTime=None, stopIfMatch=False):
        self.name = name
        if not isinstance(events, list):
            events = [events]
        self.events = events
        self.maxTime = maxTime
        if len(self.events) > 1 and not self.maxTime:
            self.maxTime = datetime.timedelta(seconds=len(self.events)-1)
        self.action = action
        self.args = args
        self.stopIfMatch = stopIfMatch

    def matches(self, events):
        """does the filter match the end of the actual events?"""
        comp = events[-len(self.events):]
        if len(comp) < len(self.events):
            return False
        if len(comp) > 1 and comp[-1].when - comp[0].when > self.maxTime:
            # the events are too far away from each other:
            return False
        return all(comp[x] == self.events[x] for x in range(0, len(comp)))

    def execute(self, event):
        """execute this filter action"""
        if 'f' in OPTIONS.debug:
            LOGGER.debug('executing filter %s' % str(self))
        if self.args:
            if isinstance(self.args, list):
                return self.action(event, *self.args) # pylint: disable=W0142
            else:
                return self.action(event, self.args)
        else:
            return self.action(event)

    def __str__(self):
        """return name"""
        if self.name:
            return self.name
        return '[%s]' % ','.join(str(x) for x in self.events)

class Hal(object):
    """base class for central definitions, to be overridden by you!"""
    def __init__(self):
        """the default lirc socket to listen on is /var/run/lirc/lircd.
        Change that by setting self.irwSocket in setup()"""
        self.filters = []
        self.events = []
        self.timers = []
        self.__timerInterval = 20
        self.irwSocket = '/var/run/lirc/lircd'
        self.setup()
        reactor.connectUNIX(self.irwSocket, IrwFactory(self))
        reactor.callLater(0, self.__checkTimers)
        reactor.run()

    def setup(self):
        """override this, not __init__"""

    def eventReceived(self, event):
        """central entry point for all events"""
        if 'e' in OPTIONS.debug:
            LOGGER.debug(str(event))
        self.events.append(event)
        matchingFilters = list(x for x in self.filters if x.matches(self.events))
        for fltr in matchingFilters:
            if fltr.matches(self.events):
                fltr.execute(event)
                if fltr.stopIfMatch:
                    break

    def addRemoteFilter(self, remote, button, action, args=None):
        """a little helper for a common use case"""
        self.filters.append(Filter(RemoteEvent(remote, button), action, args=args))

    # pylint: disable=R0913
    def addTimer(self, action, args=None, name=None, minute=None, hour=None,
           day=None, month=None, weekday=None):
        """action is a method to be called with args
        when is a python datetime object"""
        self.timers.append(Timer(action, name, args, minute, hour, day, month, weekday))

    def __checkTimers(self):
        """check and execute timers"""
        for timer in self.timers:
            timer.trigger()
        reactor.callLater(self.__timerInterval, self.__checkTimers)

class Request(Deferred):
    """we request the device to do something"""
    def __init__(self, protocol, message, timeout=5):
        """data without line eol. timeout -1 means we do not expect an answer."""
        self.protocol = protocol
        self.sendTime = None
        assert isinstance(message, Message), message
        self.message = message
        self.timeout = timeout
        Deferred.__init__(self)

    def restOfDelay(self, oldRequest):
        """the remaining time of the delay between oldRequest and self"""
        if oldRequest:
            delay = self.protocol.delay(oldRequest, self)
            if delay:
                elapsed = elapsedSince(oldRequest.sendTime)
                stillWaiting = delay - elapsed
                if stillWaiting > 0:
                    return stillWaiting
        return 0

    def __delaySending(self, dummyResult):
        """some commands leave the device in a state where it cannot
        accept more commands for some time. Since queries are mostly
        harmless, we cannot simply respect delay to previous command,
        we need to check further back in the history"""
        allRequests = [x for x in self.protocol.tasks.allRequests if x.sendTime]
        if allRequests:
            waitingAfter = sorted(allRequests, key=self.restOfDelay)[-1]
            stillWaiting = self.restOfDelay(waitingAfter)
            if stillWaiting:
                if 's' in OPTIONS.debug:
                    prevMessage = waitingAfter.message
                    delay = self.protocol.delay(waitingAfter, self)
                    LOGGER.debug('sleeping %s out of %s seconds between %s and %s' % ( \
                        stillWaiting, delay, prevMessage, self.message))
                deferred = Deferred()
                reactor.callLater(stillWaiting, deferred.callback, None)
                return deferred
        return succeed(None)

    def send(self):
        """send request to device"""
        def _send(dummyResult):
            """now the transport is open"""
            self.sendTime = datetime.datetime.now()
            assert self.protocol.transport, 'transport not set in %s' % self.protocol
            reactor.callLater(self.timeout, self.timedout)
            data = self.message.encoded + self.protocol.eol
            return self.protocol.transport.write(data)
        return self.protocol.open().addCallback(self.__delaySending).addCallback(_send)

    def timedout(self):
        """do callback(None) and log warning"""
        if self.timeout != -1 and not self.called:
            LOGGER.warning('request timed out: %s' % self)
        if not self.called:
            self.protocol.lineReceived('')

    def __str__(self):
        """for logging"""
        return '%s %s %s' % (id(self), self.protocol.name(), self.message)

class TaskQueue:
    """serializes requests for a device. If needed, delay next
    request. Problem: We should do this at a higher level. For
    Denon, if the remote sends two poweron in fast succession,
    the second one will generate a task before the Denon sends
    back the state change for the first one, so we send a second
    poweron when it is not really needed at all."""

    # TODO: use chainDeferred. When all requests are callbacked,
    # remove them - no memleak wanted

    def __init__(self):
        self.running = None
        self.queued = []
        self.allRequests = []

    def push(self, request):
        """put a task into the queue and try to run it"""
        assert isinstance(request, Request), request
        request.previous = self.allRequests[-1] if self.allRequests else None
        self.queued.append(request)
        self.allRequests = self.allRequests[-20:]
        self.allRequests.append(request)
        self.run()
        return request

    def run(self):
        """if no task is active and we have pending tasks,
        execute the next one"""
        if not self.running and self.queued:
            self.running = self.queued.pop(0)
            assert self.running
            self.running.send()

    def gotAnswer(self, msg):
        """the device returned an answer"""
        if 'r' in OPTIONS.debug:
            LOGGER.debug('gotAnswer for %s: %s' % (self.running, msg))
        running = self.running
        self.running = None
        running.callback(msg)
        self.run()

class Serializer(object):
    """a mixin class"""
    eol = '\r'
    message = Message

    def __init__(self):
        self.tasks = TaskQueue()

    def open(self): # pylint: disable=R0201
        """the device is always open"""
        return succeed(None)

    def delay(self, dummyPrevious, dummyThis): # pylint: disable=R0201
        """compute necessary delay before we can execute request"""
        return 0

    def push(self, cmd):
        """unconditionally send cmd"""
        assert isinstance(cmd, Message), cmd
        return self.tasks.push(Request(self, cmd))

    def name(self):
        """for logging messages"""
        return self.__class__.__name__.replace('Protocol','')

    def args2message(self, args):
        """convert the last argument to a Message"""
        assert len(args) in (1, 2, 3), args
        if isinstance(args[0], Event):
            event = args[0]
        else:
            event = None
        msg = args[-1]
        if not isinstance(msg, Message):
            msg = self.message(msg)
        return event, msg

class OsdCat(object):
    """lets us display OSD messages on the X server"""
    def __init__(self):
        self.__osdcat = None
        self.__lastSent = None
        self.closeTimeout = 5

    def open(self):
        """start process if not running"""
        if not self.__osdcat:
            self.__osdcat = ProcessProtocol()
            reactor.spawnProcess(self.__osdcat, 'osd_cat', args=['osdcat',
               '--align=center', '--outline=5', '--lines=1', '--delay=2', '--offset=10',
               '--font=-adobe-courier-bold-r-normal--*-640-*-*-*-*' \
               ], env={'DISPLAY': ':0'})
        reactor.callLater(self.closeTimeout, self.close)

    def close(self):
        """close the process"""
        if self.__osdcat:
            if elapsedSince(self.__lastSent) > self.closeTimeout - 1:
                self.__osdcat.transport.closeStdin()
                self.__osdcat = None

    def write(self, data):
        """write to the osd_cat process"""
        self.open()
        self.__osdcat.transport.write(data + '\n')
        self.__lastSent = datetime.datetime.now()

class GembirdProtocol(ProcessProtocol):
    # pylint: disable=W0232
    # pylint - we do not need __init__
    """we want to log gembird error messages"""

    def errReceived(self, data):
        """got stderr from sispmctl"""
        for line in data.split('\n'):
            if not line:
                # we do not want to log the copyright...
                # what sense does it make to add that
                # to an error message?
                break
            LOGGER.error('Gembird: %s' % line)

class Gembird(object):
    """use the external program sispmctl for controlling
    the Gembird USB power outlet"""
    def __init__(self, device='/dev/steckerleiste'):
        self.device = device


    def poweron(self, dummyEvent, which):
        """toggle power on&off. which is 1 2 3 4 all"""
        self.__send('-o', which)

    def poweroff(self, dummyEvent, which):
        """toggle power on&off. which is 1 2 3 4 all"""
        self.__send('-f', which)

    def toggle(self, dummyEvent, which):
        """toggle power on&off. which is 1 2 3 4 all"""
        self.__send('-t', which)

    def __send(self, *args):
        """write to the osd_cat process"""
        if 's' in OPTIONS.debug:
            LOGGER.debug('sending to Gembird: %s' % ' '.join(args))
        sisargs = ['sispmctl', '-d', self.device]
        sisargs.extend(args)
        reactor.spawnProcess(GembirdProtocol(), 'sispmctl', args=sisargs, env={'PATH': os.environ['PATH']})

def main(hal):
    """it should not be necessary to ever adapt this"""
    if OPTIONS.background:
        with daemon.DaemonContext():
            hal()
    else:
        hal()

parseOptions()
initLogger()
