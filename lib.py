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

import datetime, daemon, weakref
import logging, logging.handlers
from optparse import OptionParser

from twisted.internet import reactor
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

def parseOptions():
    """should switch to argparse when debian stable has python 2.7"""
    parser = OptionParser()
    parser.add_option('-d', '--debug', dest='debug',
        help="""DEBUG:
a sequence of characters: 's' shows data sent to appliances.
'r' shows data read from appliances in human readable form.
'e' shows events received
'p' shows data sent and read in the transfer format
'c' regularly checks request queues for zombies and logs them
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
                self.action(*self.args)
            else:
                self.action()

class Message(object):
    """holds content of a message from or to a device"""
    def __init__(self, decoded=None, encoded=None):
        assert (decoded is None) != (encoded is None), \
            'decoded:%s encoded:%s' % (decoded, encoded)
        if decoded is not None:
            assert isinstance(decoded, basestring), repr(decoded)
        if encoded is not None:
            assert isinstance(encoded, basestring), repr(encoded)
        self._encoded = None
        self._decoded = None
        self.isQuestion = False
        self.when = datetime.datetime.now()
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

    def __str__(self):
        """use the human readable form for logging"""
        result = self.humanCommand()
        if not result:
            return 'None'
        if self.value():
            result += ':%s' % self.value()
        else:
            result += '?'
        return result

    def __eq__(self, other):
        """are they identical?"""
        return self.decoded == other.decoded

class Filter(object):
    """a filter always has a name. events is a single event or a list of events.
    Attributes:
        maxTime        of type timedelta, with default = len(events) seconds.
        stopIfMatch    if True and this Filter matches, do not look at following filters
    """
    def __init__(self, events, action, *args, **kwargs):
        self.action = action
        self.args = args
        self.kwargs = kwargs
        if not isinstance(events, list):
            events = [events]
        for event in events:
            assert type(event) != Message
        self.events = events
        self.maxTime = None
        self.stopIfMatch = False
        if len(self.events) > 1 and not self.maxTime:
            self.maxTime = datetime.timedelta(seconds=len(self.events)-1)

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
        return self.action(event, *self.args, **self.kwargs)

    def __str__(self):
        """return name"""
        return '[%s]' % ','.join(str(x) for x in self.events)

class Hal(object):
    """base class for central definitions, to be overridden by you!"""
    def __init__(self):
        self.filters = []
        self.events = []
        self.timers = []
        self.__timerInterval = 20
        self.setup()
        reactor.callLater(0, self.__checkTimers)
        reactor.run()

    def setup(self):
        """override this, not __init__"""

    def eventReceived(self, event):
        """central entry point for all events"""
        if 'e' in OPTIONS.debug:
            LOGGER.debug('Hal.eventReceived:%s' % str(event))
        self.events.append(event)
        matchingFilters = list(x for x in self.filters if x.matches(self.events))
        for fltr in matchingFilters:
            if fltr.matches(self.events):
                fltr.execute(event)
                if fltr.stopIfMatch:
                    break

    def addFilter(self, source, msg, action, *args, **kwargs):
        """a little helper for a common use case"""
        self.filters.append(Filter(source.message(msg), action, *args, **kwargs))

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
        Serializer.check()
        reactor.callLater(self.__timerInterval, self.__checkTimers)

class Request(Deferred):
    """we request the device to do something"""
    def __init__(self, protocol, message, timeout=5):
        """data without line eol. timeout -1 means we do not expect an answer."""
        self.protocol = protocol
        self.createTime = datetime.datetime.now()
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
        def send1(dummyResult):
            """now the transport is open"""
            self.sendTime = datetime.datetime.now()
            reactor.callLater(self.timeout, self.timedout)
            data = self.message.encoded + self.protocol.eol
            return self.protocol.write(data)
        return self.protocol.open().addCallback(self.__delaySending).addCallback(send1)

    def timedout(self):
        """do callback(None) and log warning"""
        if self.timeout != -1 and not self.called:
            LOGGER.warning('request timed out: %s' % self)
        if not self.called:
            self.protocol.lineReceived('')

    def __str__(self):
        """for logging"""
        if self.sendTime:
            comment = 'sent %s seconds ago' % elapsedSince(self.sendTime)
        else:
            elapsed = elapsedSince(self.createTime)
            if elapsed < 0.1:
                comment = ''
            else:
                comment = 'unsent, created %s seconds ago' % elapsedSince(self.createTime)
        return '%s %s %s %s' % (id(self), self.protocol.name(), self.message, comment)

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
        if 'c' in OPTIONS.debug:
            LOGGER.debug('queued: %s' % request)
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
    """
       a mixin class, presenting a unified interface
       to the devices.

       Attributes:

       answersAsEvents: Some devices can send status changes as events,
                        even if the status has been changed by other means
                        like the original remote control or the front elements.
                        Of the currently implemented devices, only the Denon
                        can do that. Those events are passed to the global
                        event handler. If this flag is set, all answers are
                        processed as usual and - in addition - passed to the
                        global event handler. Example usage: If Denon changes
                        volume, the global event handler will display the new
                        volume on the TV. With this flag set, that also
                        happens for volume changes done by halirc.
    """
    eol = '\r'
    message = Message
    # __instances holds weakrefs to Serializer instances. We do not bother
    # to ever remove items since a Serializer is normally never deleted, but
    # just in case we use weakrefs anyway
    __instances = []

    def __init__(self, hal):
        self.hal = hal
        self.tasks = TaskQueue()
        self.answersAsEvents = False
        self.__instances.append(weakref.ref(self))

    def open(self): # pylint: disable=R0201
        """the device is always open"""
        return succeed(None)

    def delay(self, dummyPrevious, dummyThis): # pylint: disable=R0201
        """compute necessary delay before we can execute request"""
        return 0

    def write(self, data):
        """default is writing to transport"""
        if 'p' in OPTIONS.debug:
            LOGGER.debug('WRITE to %s: %s' % (self.name(), repr(data)))
        self.transport.write(data) # pylint: disable=E1101
        # pylint Serializer by default is a mixin to a Protocol
        # which defines transport

    def defaultInputHandler(self, data):
        """we got a line from a device"""
        if 'p' in OPTIONS.debug:
            LOGGER.debug('READ from %s: %s' % (self.name(), repr(data)))
        msg = self.message(encoded=data)
        isAnswer = self.tasks.running and \
            self.tasks.running.message.humanCommand() == msg.humanCommand()
        if isAnswer:
            self.tasks.gotAnswer(msg)
        if not isAnswer or self.answersAsEvents:
            self.hal.eventReceived(msg)

    def push(self, cmd):
        """unconditionally send cmd"""
        assert isinstance(cmd, Message), cmd
        return self.tasks.push(Request(self, cmd))

    def name(self):
        """for logging messages"""
        return self.__class__.__name__.replace('Protocol','')

    def args2message(self, *args):
        """convert the last argument to a Message"""
        assert len(args) in (1, 2, 3), args
        if isinstance(args[0], Message):
            event = args[0]
        else:
            event = None
        msg = args[-1]
        if not isinstance(msg, Message):
            msg = self.message(msg)
        return event, msg

    def ask(self, *args):
        """ask the device for a value"""
        _, msg = self.args2message(*args)
        # strip value from message:
        msg = self.message(msg.humanCommand())
        return self.push(msg)

    def send(self, *args):
        """check the current device value and send the wanted
        new value.
        """
        _, msg = self.args2message(*args)
        def got(result):
            """now we know the current value"""
            if result.value() != msg.value():
                return self.push(msg)
            else:
                return succeed(None)
        return self.ask(msg).addCallback(got)

    @staticmethod
    def check():
        """check for requests that should not exist anymore"""
        if not 'c' in OPTIONS.debug:
            return
        for ref in Serializer.__instances:
            serializer = ref()
            if serializer:
                for request in serializer.tasks.queued:
                    LOGGER.debug('open: %s' % request)

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

def main(hal):
    """it should not be necessary to ever adapt this"""
    if OPTIONS.background:
        with daemon.DaemonContext():
            hal()
    else:
        hal()

parseOptions()
initLogger()
