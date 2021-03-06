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

import datetime, daemon, weakref, types, sys
import logging, logging.handlers
from optparse import OptionParser

from twisted.internet import reactor
from twisted.internet.protocol import ProcessProtocol
from twisted.internet.defer import Deferred, succeed
from twisted.protocols.basic import LineOnlyReceiver
from twisted.conch.telnet import Telnet

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

def scanDeviceIds():
    """TODO: this should happen dynamically, not hard coded"""
    Serializer.debugIds.append('Lirc')
    Serializer.debugIds.append('VDR')
    Serializer.debugIds.append('Yamaha')
    Serializer.debugIds.append('Denon')
    Serializer.debugIds.append('Gembird')
    Serializer.debugIds.append('LGTV')
    Serializer.debugIds.append('Pioneer')

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
'f' shows trigger info
't' shows timing info
             """, default='', metavar='DEBUG')
    parser.add_option('-b', '--background', dest='background',
        action="store_true", default=False,
        help="run in background. Logging goes to the syslogs.")
    parser.add_option('-D', '--device', dest='device',
        help="""Show only debug messages about a specific device.
If not given, show all.
DEVICE: any of {}
        """.format(' '.join(Serializer.debugIds), default='', metavar='DEVICE'))
    global OPTIONS # pylint: disable=W0603
    OPTIONS = parser.parse_args()[0]
    if OPTIONS.debug == 'all':
        OPTIONS.debug = 'srepcft'
    if not OPTIONS.device:
        OPTIONS.device = Serializer.debugIds
    else:
        OPTIONS.device = list([OPTIONS.device])

def initLogger():
    """logging goes to stderr when running in foregrund, else
    to syslog"""
    global LOGGER # pylint: disable=W0603
    LOGGER = logging.getLogger('halirc')
    if OPTIONS.background:
        handler = logging.handlers.SysLogHandler('/dev/log')
    else:
        handler = logging.FileHandler('halirc.log')
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.DEBUG)
    if OPTIONS.background:
        # if we generate a ton of same messages, give syslog a change
        # to reduce log file output by always writing exactly the same msg
        formatter = logging.Formatter("%(name)s: %(levelname)s %(message)s")
    else:
        formatter = logging.Formatter("%(asctime)s %(name)s: %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.info('halirc started with {}'.format(' '.join(sys.argv)))
    return LOGGER

def logDebug(obj, debugFlag, msg):
    """log something about obj"""
    if not debugFlag or debugFlag in OPTIONS.debug:
        if obj:
            if obj.__class__.__name__ in OPTIONS.device:
                LOGGER.debug(msg)
        else:
            if any(x in msg for x in OPTIONS.device):
                LOGGER.debug(msg)

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

    def execute(self):
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
            'decoded:{} encoded:{}'.format(decoded, encoded)
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
        """initialise decoded / encoded. Must be overridden
        if both differ."""
        self._decoded = self._encoded = decoded or encoded

    def humanCommand(self):
        """the human readable command"""
        return self.command()

    def answerMatches(self, answer):
        """does the answer from the device match this message?"""
        return self.humanCommand() == answer.humanCommand()

    def __str__(self):
        """use the human readable form for logging"""
        if self.humanCommand:
            return '{}: {} value:{}'.format(self.__class__.__name__, self.humanCommand(),
                self.value() or '?')

    def __eq__(self, other):
        """are they identical?"""
        return self.decoded == other.decoded

    def matches(self, other):
        """used for triggers"""
        if self.isQuestion or other.isQuestion:
            return self.humanCommand() == other.humanCommand()
        else:
            return self == other

class Trigger(object):
    """a trigger always has a name. parts is a single event or a list of events.
       parts will be compared with the actual received events.
    Attributes:
        maxTime        of type timedelta, with default = len(parts) seconds.
        stopIfMatch    Default is False. If True and this Trigger matches, do not
                       look at following triggers
        mayRepeat      Default is False. If True, the trigger will not execute
                       if it is the last previously executed trigger
    """
    running = None
    queued = []
    previousExecuted = None
    longRunCancellerStarted = False

    def __init__(self, parts, action, *args, **kwargs):
        self.action = action
        assert action
        self.args = args
        self.kwargs = kwargs
        if not isinstance(parts, list):
            parts = [parts]
        for event in parts:
            assert type(event) != Message
        self.parts = parts
        self.event = None # the current event having triggered this trigger
        self.maxTime = None
        self.stopIfMatch = False
        self.mayRepeat = False
        if len(self.parts) > 1 and not self.maxTime:
            self.maxTime = datetime.timedelta(seconds=len(self.parts)-1)
        if not Trigger.longRunCancellerStarted:
            Trigger.longRunCancellerStarted = True
            # call this only once
            reactor.callLater(1, Trigger.cancelLongRun)

    def matches(self, events):
        """does the trigger match the end of the actual events?"""
        comp = events[-len(self.parts):]
        if len(comp) < len(self.parts):
            return False
        if len(comp) > 1 and comp[-1].when - comp[0].when > self.maxTime:
            # the events are too far away from each other:
            return False
        return all(comp[x].matches(self.parts[x]) for x in range(0, len(comp)))

    def execute(self, event):
        """execute this trigger action"""
        if not self.mayRepeat and id(self) == id(Trigger.previousExecuted):
            repeatMaxTime = datetime.timedelta(seconds=0.5)
            if event.when - Trigger.previousExecuted.event.when < repeatMaxTime:
                return
        logDebug(None, 'f', 'ACTION queue:{}'.format(str(self)))
        self.event = event
        Trigger.queued.append(self)
        Trigger.previousExecuted = self
        if Trigger.running:
            logDebug(None, None, 'When starting trigger {}, older trigger still runs:{}'.format(self, Trigger.running))
        self.run()

    @staticmethod
    def run():
        """if no trigger action is currently running and we have some in the
        queue, start the next one"""
        if Trigger.running:
            return
        if Trigger.queued:
            trgr = Trigger.running = Trigger.queued.pop(0)
            assert trgr.action
            logDebug(None, 'f', 'ACTION start:{}'.format(str(trgr)))
            act = trgr.action(trgr.event, *trgr.args, **trgr.kwargs)
            assert act, 'Trigger {} returns None'.format(str(trgr))
            return act.addCallback(trgr.executed).addErrback(trgr.notExecuted)

    def executed(self, dummyResult):
        """now the trigger has finished. TODO: error path"""
        logDebug(None, 'f', 'ACTION done :{} '.format(self))
        Trigger.running = None
        self.run()

    def notExecuted(self, result):
        """now the trigger has finished. TODO: error path"""
        LOGGER.error('ACTION {} had error :{}'.format((self, str(result))))
        Trigger.running = None
        Trigger.queued = []
        self.run()

    @classmethod
    def cancelLongRun(cls):
        """after 10 seconds, cancel a running request"""
        if cls.running:
            elapsed = elapsedSince(cls.running.event.when)
            logDebug(None, 't', '{} running since {} seconds'.format(
              cls.running, elapsed))
            if elapsed > 10:
                LOGGER.error('ACTION {} cancelled after {} seconds'.format(
                    cls.running, elapsed))
                cls.running = None
        reactor.callLater(1, Trigger.cancelLongRun)

    def __str__(self):
        """return name"""
        if isinstance(self.action, types.FunctionType):
            action = self.action.__name__
        else:
            action = '.'.join([self.action.im_class.__name__, self.action.__name__])
        result = '%s %s: %s' % (id(self) % 10000, ','.join(str(x) for x in self.parts), action)
        if self.args:
            result += ' args=%s' % str(self.args)
        if self.kwargs:
            result += ' kwargs=%s' % str(self.kwargs)
        return result

class Hal(object):
    """base class for central definitions, to be overridden by you!"""
    def __init__(self):
        self.triggers = []
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
        triggers = list()
        self.events.append(event)
        matchingTriggers = list(x for x in self.triggers if x.matches(self.events))
        for trgr in matchingTriggers:
            if trgr.matches(self.events):
                triggers.append(str(trgr))
                trgr.execute(event)
                if trgr.stopIfMatch:
                    break
        if triggers:
            for trgr in triggers:
                logDebug(None, 'e', 'received {}, triggers {}'.format(event, trgr))
        else:
            logDebug(None, 'e', 'received {}, triggers nothing'.format(event))

    def addTrigger(self, source, msg, action, *args, **kwargs):
        """a little helper for a common use case"""
        trgr = Trigger(source.message(msg), action, *args, **kwargs)
        self.triggers.append(trgr)
        return trgr

    def addRepeatableTrigger(self, source, msg, action, *args, **kwargs):
        """a little helper for a common use case"""
        trgr = Trigger(source.message(msg), action, *args, **kwargs)
        trgr.mayRepeat = True
        logDebug(None, None, 'appending trigger {}'.format(trgr))
        self.triggers.append(trgr)
        return trgr

    # pylint: disable=R0913
    def addTimer(self, action, args=None, name=None, minute=None, hour=None,
           day=None, month=None, weekday=None):
        """action is a method to be called with args
        when is a python datetime object"""
        self.timers.append(Timer(action, name, args, minute, hour, day, month, weekday))

    def __checkTimers(self):
        """check and execute timers"""
        for timer in self.timers:
            timer.execute()
        Serializer.check()
        reactor.callLater(self.__timerInterval, self.__checkTimers)

class Request(Deferred):
    """we request the device to do something"""
    def __init__(self, protocol, message, maxWaitSeconds=None):
        """data without line eol. maxWaitSeconds -1 means we do not expect an answer."""
        if maxWaitSeconds is None:
            maxWaitSeconds = 5
        self.protocol = protocol
        self.message = message
        self.maxWaitSeconds = maxWaitSeconds
        self.createTime = datetime.datetime.now()
        self.sendTime = None
        self.answerTime = datetime.datetime.now() if maxWaitSeconds == -1 else None
        assert isinstance(message, Message), message
        Deferred.__init__(self)

    def restOfDelay(self, oldRequest):
        """the remaining time of the delay between oldRequest and self"""
        if oldRequest:
            delay = self.protocol.delay(oldRequest, self)
            if delay:
                elapsed = elapsedSince(oldRequest.sendTime)
                stillWaiting = delay - elapsed
                if stillWaiting > 0:
                    logDebug(self.protocol, 't',
                        '{} still waiting {} seconds until delay {} after {} is complete'.format(
                        self, stillWaiting, delay, oldRequest))
                    return stillWaiting
        return 0

    def __delaySending(self, dummyResult):
        """some commands leave the device in a state where it cannot
        accept more commands for some time. Since queries are mostly
        harmless, we cannot simply respect delay to previous command,
        we need to check further back in the history"""
        if not self.protocol.connected:
            logDebug('delay Sending for 0.1 second, we are not connected', 't', self.message)
            return sleep(0.1).addCallback(self.__delaySending)
        allRequests = [x for x in self.protocol.tasks.allRequests if x.sendTime]
        # sometimes we must wait even if the previous command has been
        # acked. Needed for LGTV after poweron.
        if allRequests:
            waitingAfter = sorted(allRequests, key=self.restOfDelay)[-1]
            stillWaiting = self.restOfDelay(waitingAfter)
            if stillWaiting:
                logDebug(self.protocol, 't', 'sleeping {} out of {} seconds between {} and {}'.format(
                    stillWaiting, self.protocol.delay(waitingAfter, self), waitingAfter.message, self.message))
                deferred = Deferred()
                reactor.callLater(stillWaiting, deferred.callback, None)
                return deferred
        return succeed(None)

    def send(self):
        """send request to device"""
        def send1(dummyResult):
            """now the transport is open"""
            self.sendTime = datetime.datetime.now()
            data = self.message.encoded + self.protocol.eol
            logDebug(self.protocol, 'p', 'WRITE {}: {}'.format(self, repr(data)))
            return self.protocol.write(data)
        def sent(dummy, sendDeferred):
            """off it went"""
            Trigger.running = None
            if self.maxWaitSeconds > 0:
                reactor.callLater(self.maxWaitSeconds, timedout, sendDeferred)
        def timedout(timedoutDeferred):
            """did we time out?"""
            if self.answerTime:
                return
            LOGGER.error('Timeout on {}, cancelling'.format(self))
            timedoutDeferred.cancel()
            Trigger.running = None
            Trigger.queued = []
            self.errback(Exception('request timed out: {}'.format(self)))
        sendDeferred = self.protocol.open()
        sendDeferred.addCallback(self.__delaySending).addCallback(send1).addCallback(sent, sendDeferred)
        if self.maxWaitSeconds < 0.0:
            sendDeferred.addCallback(self._donotwait)
        return sendDeferred

    def callback(self, *args, **kwargs):
        """request fulfilled"""
        Deferred.callback(self, *args, **kwargs)

    def _donotwait(self, dummyResult):
        """do callback(None) and log warning"""
        assert self.maxWaitSeconds == -1, "_donotwait: maxWaitSeconds {} should be -1".format(self.maxWaitSeconds)
        Trigger.running = None
        self.callback(None)

    def __str__(self):
        """for logging"""
        if self.sendTime:
            comment = 'sent %.3f seconds ago' % elapsedSince(self.sendTime)
        else:
            elapsed = elapsedSince(self.createTime)
            if elapsed < 0.1:
                comment = ''
            else:
                comment = 'unsent, created %.3f seconds ago' % elapsedSince(self.createTime)
        return '%s %s %s %s maxWaitSeconds=%s' % (
                id(self) % 10000, self.protocol.name(), self.message,
                comment, 'nowait' if self.maxWaitSeconds == -1 else self.maxWaitSeconds)

class TaskQueue(object):
    """serializes requests for a device. If needed, delay next
    request. Problem: We should do this at a higher level. For
    Denon, if the remote sends two poweron in fast succession,
    the second one will generate a task before the Denon sends
    back the state change for the first one, so we send a second
    poweron when it is not really needed at all."""

    def __init__(self, device):
        self.device = device
        self.running = None
        self.queued = []
        self.allRequests = []

    def push(self, request):
        """put a task into the queue and try to run it"""
        assert isinstance(request, Request), request
        request.previous = self.allRequests[-1] if self.allRequests else None
        self.queued.append(request)
        logDebug(self.device, 'c', 'queued for {}: {}'.format(self.device, request))
        self.allRequests = self.allRequests[-20:]
        self.allRequests.append(request)
        request.addErrback(self.failed)
        self.run()
        return request

    def failed(self, result):
        """a request failed. Clear the queue."""
        LOGGER.error('Request {} failed with` {}, clearing queue for {}'.format(
            id(self) % 10000, result, self.running.protocol.name()))
        self.running = None
        self.queued = []

    def run(self):
        """if no task is active and we have pending tasks,
        execute the next one"""
        def sent(dummy):
            """off it went"""
            self.running = None
            reactor.callLater(0, self.run) # do not call directly, no recursion
        if not self.running and self.queued:
            self.running = self.queued.pop(0)
            if self.running.maxWaitSeconds == -1:
                return self.running.send().addCallback(sent).addErrback(self.failed)
            else:
                return self.running.send().addErrback(self.failed)

    def gotAnswer(self, msg):
        """the device returned an answer"""
        logDebug(self.device, 'r', 'gotAnswer for {}: {}'.format(self.running, msg))
        self.running.answerTime = datetime.datetime.now()
        running = self.running
        self.running = None
        running.callback(msg)
        self.run()

def sleep(secs):
    """returns a Deferred which fires after secs"""
    deferred = Deferred()
    reactor.callLater(secs, deferred.callback, None)
    return deferred

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

       outlet: None or a power outlet onto which this device is connected
    """
    eol = '\r'
    message = Message
    debugIds = list()
    # __instances holds weakrefs to Serializer instances. We do not bother
    # to ever remove items since a Serializer is normally never deleted, but
    # just in case we use weakrefs anyway
    __instances = []
    poweronCommands = []

    def __init__(self, hal, outlet=None):
        self.hal = hal
        self.outlet = outlet
        self.tasks = TaskQueue(self)
        self.answersAsEvents = False
        self.__instances.append(weakref.ref(self))
        self.bootDelay = 1     # time needed for cold boot
        self.shutdownDelay = 1 # time needed for shutdown into standby
        self.connected = True

    def open(self): # pylint: disable=R0201
        """the device is always open"""
        return succeed(None)

    @staticmethod
    def delay(dummyPrevious, dummyThis):
        """compute necessary delay before we can execute request"""
        return 0

    def write(self, data):
        """default is writing to transport"""
        self.transport.write(data) # pylint: disable=E1101
        # pylint Serializer by default is a mixin to a Protocol
        # which defines transport

    def defaultInputHandler(self, data):
        """we got a line from a device"""
        logDebug(self, 'p', 'READ {}: {}'.format(self.name(), repr(data)))
        msg = self.message(encoded=data)
        isAnswer = self.tasks.running and \
            self.tasks.running.message.answerMatches(msg)
        if isAnswer:
            self.tasks.gotAnswer(msg)
        if not isAnswer or self.answersAsEvents:
            self.hal.eventReceived(msg)
        return msg

    def push(self, *args):
        """unconditionally send cmd"""
        _, msg = self.args2message(*args)
        assert isinstance(msg, Message), msg
        return self.tasks.push(Request(self, msg))

    def pushBlind(self, *args):
        """unconditionally send cmd, do not expect an answer"""
        _, msg = self.args2message(*args)
        assert isinstance(msg, Message), msg
        return self.tasks.push(Request(self, msg, maxWaitSeconds=-1))

    def name(self):
        """for logging messages"""
        return self.__class__.__name__.replace('Protocol', '')

    def args2message(self, *args):
        """convert the last argument to a Message"""
        assert len(args) in (1, 2, 3), args
        if len(args) > 1 and isinstance(args[0], Message):
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

    def poweron(self, *args):
        """power on this device"""
        def hasPower(*dummyArgs):
            """device should have power"""
            # pylint: disable=W0142
            return sleep(self.bootDelay).addCallback(self._poweron, *args)
        if self.outlet:
            return self.outlet.poweron(*args).addBoth(hasPower)
        else:
            return self._poweron(*args)

    def standby(self, *args):
        """put into standby mode"""
        def isOff(*dummyArgs):
            """device should be shutdown"""
            # pylint: disable=W0142
            return sleep(self.shutdownDelay).addCallback(self.outlet.standby, *args)
        result = self._standby(*args)
        if self.outlet:
            result.addBoth(isOff)
        return result

    def _poweron(self, *dummyArgs): # pylint: disable=no-self-use
        """to be overridden by the specific device"""
        return succeed(None)

    def _standby(self, *dummyArgs): # pylint: disable=no-self-use
        """to be overridden by the specific device"""
        return succeed(None)

    def reallySend(self, *args):
        """send command without checking"""
        _, msg = self.args2message(*args)
        return self.push(msg)

    def _send(self, *args):
        """check the current device value and send the wanted
        new value.
        """
        _, msg = self.args2message(*args)
        def got(result):
            """now we know the current value"""
            if not result or result.value() != msg.value():
                return self.push(msg)
            else:
                return succeed(None)
        return self.ask(msg).addCallback(got)

    def send(self, *args):
        """check the current device value and send the wanted
        new value. But first poweron if needed.
        """
        _, msg = self.args2message(*args)
        if msg.humanCommand() in self.poweronCommands:
            return self.poweron().addCallback(self._send, msg)
        else:
            return self._send(*args)

    @staticmethod
    def check():
        """check for requests that should not exist anymore"""
        if not 'c' in OPTIONS.debug:
            return
        for ref in Serializer.__instances:
            serializer = ref()
            if serializer:
                for request in serializer.tasks.queued:
                    LOGGER.debug('open: {}'.format(request))

class OsdCat(object):
    """lets us display OSD messages on the X server"""
    def __init__(self):
        self.__osdcat = None
        self.__lastSent = None
        self.closeTimeout = 20

    def open(self):
        """start process if not running"""
        if not self.__osdcat:
            self.__osdcat = ProcessProtocol()
            reactor.spawnProcess(self.__osdcat, '/usr/bin/osd_cat', args=['osd_cat',
               '--align=center', '--outline=5', '--lines=1', '--delay=2', '--offset=10',
               '--font=-adobe-courier-bold-r-normal--*-640-*-*-*-*' \
               ], env={'DISPLAY': ':0'})
            logDebug(self, 'p', 'OsdCat started process')
        reactor.callLater(self.closeTimeout, self.close)

    def close(self):
        """close the process"""
        if self.__osdcat:
            if elapsedSince(self.__lastSent) > self.closeTimeout - 1:
                self.__osdcat.transport.closeStdin()
                self.__osdcat = None
                logDebug(self, 'p', 'OsdCat stopped process')

    def write(self, data):
        """write to the osd_cat process"""
        self.open()
        logDebug(self, 'p', 'WRITE to OsdCat: {}'.format(repr(data)))
        self.__osdcat.transport.write(data + '\n')
        self.__lastSent = datetime.datetime.now()
        return succeed(None)

    def __str__(self):
        return 'OsdCat'

    def __repr__(self):
        return 'OsdCat'

class SimpleTelnet(LineOnlyReceiver, Telnet):
    """just what we normally need"""
    # pylint: disable=R0904
    # pylint finds too many public methods

    delimiter = '\r\n'

    def __init__(self):
        Telnet.__init__(self)

    def lineReceived(self, line):
        """must be overridden"""

    def disableRemote(self, option):
        """disable a remote option"""

    def disableLocal(self, option):
        """disable a local option"""

def main(hal):
    """it should not be necessary to ever adapt this"""
    if OPTIONS.background:
        with daemon.DaemonContext():
            hal()
    else:
        hal()

scanDeviceIds()
parseOptions()
initLogger()
