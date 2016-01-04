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

from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory
from twisted.internet.defer import succeed


from lib import Serializer, SimpleTelnet, Message, LOGGER, elapsedSince

class YamahaMessage(Message):
    """holds content of a message from or to Yamaha"""
    def __init__(self, decoded=None, encoded=None):
        """for the Yamaha we only use the machine form, its
        readability is acceptable"""
        encoded = encoded or decoded
        decoded = None
        Message.__init__(self, decoded, encoded)
        self.isQuestion = self.value() == '?'

    def command(self):
        """the human readable command"""
        return self._encoded.split('=')[0]

    def value(self):
        """the human readable command"""
        return self._encoded.split('=')[1] if '=' in self._encoded else '?'

    def answerMatches(self, answer):
        return Message.answerMatches(self, answer) and self.isQuestion

    def __str__(self):
        return self._encoded

    def __repr__(self):
        return 'YamahaMessage(%s)' % self._encoded

class YamahaProtocol(SimpleTelnet):
    """talk to Yamaha"""
    # pylint: disable=R0904
    # pylint finds too many public methods

    def __init__(self):
        self.wrapper = None
        self.status = {}
        SimpleTelnet.__init__(self)

    def lineReceived(self, line):
        """we got a full line from Yamaha"""
        msg = Serializer.defaultInputHandler(self.wrapper, line)
#        if 'p' in OPTIONS.debug:
#            LOGGER.debug('READ from %s: %s' % (self.wrapper.name(), repr(line)))
#        msg = YamahaMessage(line)
        self.status[msg.command()] = msg.value()
#        if self.wrapper.tasks.running:
#            self.wrapper.tasks.gotAnswer(msg)

class Yamaha(Serializer):

    """talks to Yamaha. This is a wrapper around the Telnet protocol
    becaus we want to automatically close the connection after
    some timeout and automatically reopen it when needed. Yamaha
    can only handle one client simultaneously."""

    # TODO: an event generator watching syslog for things like
    # switching channel
    eol = '\r\n'
    message = YamahaMessage

    def __init__(self, hal, host, port=50000, outlet=None):
        Serializer.__init__(self, hal, outlet)
        self.host = host
        self.port = port
        self.protocol = None
        self.mutedVolume = None
        self.answersAsEvent = True
        self.closeTimeout = 2000000
        self.open()

    def open(self):
        """open connection if not open"""
        def gotProtocol(result):
            """now we have a a connection, save it"""
            self.protocol = result
            self.protocol.wrapper = self
            self.ping()
        if not self.protocol:
            LOGGER.debug('opening Yamaha')
            point = TCP4ClientEndpoint(reactor, self.host, self.port)
            factory = ClientFactory()
            factory.protocol = YamahaProtocol
            result = point.connect(factory).addCallback(gotProtocol)
        else:
            result = succeed(None)
        reactor.callLater(self.closeTimeout, self.close)
        return result

    def ping(self):
        """keep connection alive or the yamaha closes it"""
        LOGGER.debug('pinging Yamaha')
        self.write('@SYS:INPNAMEPHONO=PHONO\r\n')
        reactor.callLater(10, self.ping)

    def close(self):
        """close connection if open"""
        if self.protocol:
            if not (self.tasks.running or self.tasks.queued):
                if elapsedSince(self.tasks.allRequests[-1].sendTime) > self.closeTimeout - 1:
                    LOGGER.debug('closing Yamaha')
                    self.protocol.transport.loseConnection()
                    self.protocol = None

    @staticmethod
    def delay(previous, dummyThis):
        """do we need to wait before sending this command?"""
        prevCmd = previous.message.humanCommand() if previous else ''
        if not previous.message.isQuestion and prevCmd == '@MAIN:PWR':
            return 1
        else:
            return 0.05 # Manual says always want 100 milliseconds

    def write(self, data):
        if not self.protocol:
            raise Exception('yamaha.write: have no protocol')
        if not self.protocol.transport:
            raise Exception('yamaha.write: have no protocol.transport')
        return self.protocol.transport.write(data)

    def send(self, *args):
        """unconditionally send cmd"""
        _, msg = self.args2message(*args)
        print 'amaha.send:', msg
        if msg.value() == '?':
            return self.push(msg)
        else:
            return self.pushBlind(msg)

    def ask(self, *args):
        argList = list(args)
        argList[-1] += '=?'
        _, msg = self.args2message(*argList) # pylint: disable=star-args
        return self.push(msg)

    def _poweron(self, *dummyArgs):
        """power on the Yamaha"""
        return self.send('@MAIN:PWR=On')

    def _standby(self, *dummyArgs):
        """standby the Yamaha"""
        return self.send('@MAIN:PWR=Standby')

    def volume(self, dummyResult, newValue):
        """change volume up or down or to a discrete value"""
        def _volume1(result, newValue):
            """result is On or Standby"""
            if not result or result.value() != 'On':
                return succeed(None)
            if self.mutedVolume:
                return self.mute()
            else:
                return self.send('@MAIN:VOL=%s' % newValue)
        return self.ask('@MAIN:PWR').addCallback(_volume1, newValue)

    def mute(self, dummyResult=None):
        """toggle between mute/unmuted"""
        def _mute1(result):
            """result is ON or STANDBY"""
            if result.value() != 'On':
                return succeed(None)
            if self.mutedVolume:
                newMV = self.mutedVolume
                self.mutedVolume = None
                return self.pushBlind('@MAIN:VOL=%s' % newMV)
            return self.ask('@MAIN:VOL').addCallback(_mute2)
        def _mute2(result):
            """result is the volume before unmuting"""
            self.mutedVolume = float(result.value())
            if self.mutedVolume < -50.1:
                # denon was muted when halirc started
                self.mutedVolume = None
                newMV = -40.0
            else:
                newMV = -55.0
            return self.pushBlind('@MAIN:VOL=%.1f' % newMV)
        return self.ask('@MAIN:PWR').addCallback(_mute1)
