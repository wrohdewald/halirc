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


from lib import Serializer, SimpleTelnet, Message, LOGGER, logDebug

class PioneerMessage(Message):
    """holds content of a message from or to Pioneer"""
    def __init__(self, decoded=None, encoded=None):
        """for the Pioneer we only use the machine form, its
        readability is acceptable"""
        Message.__init__(self, decoded, encoded)

    def command(self):
        """the human readable command"""
        return self._encoded if self._encoded else ''

    def value(self):
        """the human readable command"""
        return self._encoded if self._encoded else ''

class PioneerProtocol(SimpleTelnet):
    """talk to Pioneer"""
    # pylint: disable=R0904
    # pylint finds too many public methods

    def __init__(self):
        self.wrapper = None # TODO: needed?
        SimpleTelnet.__init__(self)

    def lineReceived(self, line):
        """we got a full line from Pioneer"""
        logDebug(self, 'p', 'READ from {}: {}'.format(self.wrapper.name(), repr(line)))
        if self.wrapper.tasks.running:
            self.wrapper.tasks.gotAnswer(PioneerMessage(line))
        else:
            LOGGER.error('Pioneer sent data without being asked:{}'.format(line))

class Pioneer(Serializer):

    """talks to Pioneer. This is a wrapper around the Telnet protocol
    becaus we want to automatically close the connection after
    some timeout and automatically reopen it when needed. Pioneer
    can only handle one client simultaneously."""

    # TODO: an event generator watching syslog for things like
    # switching channel
    eol = '\r\n'
    message = PioneerMessage

    def __init__(self, hal, host, port=8102, outlet=None):
        Serializer.__init__(self, hal, outlet)
        self.host = host
        self.port = port
        self.protocol = None

    def open(self):
        """open connection if not open"""
        def gotProtocol(result):
            """now we have a a connection, save it"""
            self.protocol = result
            self.protocol.wrapper = self
        def gotNoProtocol(result):
            """something went wrong"""
            msg = 'Pioneer: %s' % result.getErrorMessage()
            LOGGER.error(msg)
            raise Exception(msg)
        point = TCP4ClientEndpoint(reactor, self.host, self.port)
        factory = ClientFactory()
        factory.protocol = PioneerProtocol
        return point.connect(factory).addCallback(gotProtocol).addErrback(gotNoProtocol)

    @staticmethod
    def delay(previous, dummyThis):
        """do we need to wait before sending this command?"""
        cmd = previous.message.humanCommand() if previous else ''
        if cmd == 'PN':
            return 5

    def write(self, data):
        if not self.protocol:
            raise Exception('pioneer.write: have no protocol')
        if not self.protocol.transport:
            raise Exception('pioneer.write: have no protocol.transport')
        self.protocol.transport.write(data)

    def send(self, *args):
        """unconditionally send cmd"""
        _, msg = self.args2message(*args)
        return self.push(msg)

    def _poweron(self, *dummyArgs):
        """power on the Pioneer"""
        return self.send('PN')

    def _standby(self, *dummyArgs):
        """standby the Pioneer"""
        return self.send('PF')

    def play(self, *dummyArgs):
        """if tray is open, close it first"""
        def gotStatus(result):
            """now we have pioneer status"""
            if not result:
                return
            if result.value() == 'P00':
                return self.send('CO') # I have auto playback defined in player setup
            else:
                return self.send('PL')
        return self.ask('?P').addCallback(gotStatus)
