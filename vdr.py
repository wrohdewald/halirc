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

from twisted.protocols.basic import LineOnlyReceiver
from twisted.conch.telnet import Telnet
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.internet.defer import Deferred, succeed
from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory


from lib import Serializer, Message, OPTIONS, LOGGER, elapsedSince

class VdrMessage(Message):
    """holds content of a message from or to Vdr"""
    def __init__(self, decoded=None, encoded=None):
        """for the Denon we only use the machine form, its
        readability is acceptable"""
        Message.__init__(self, decoded, encoded)

    def _setAttributes(self, decoded, encoded):
        self._decoded = self._encoded = decoded or encoded

    def command(self):
        """the human readable command"""
        # TODO: subcommands
        return self._encoded[:4] if self._encoded else ''

    def value(self):
        """the human readable command"""
        # TODO: subcommands
        return self._encoded[4:] if self._encoded else ''

class VdrProtocol(LineOnlyReceiver, Telnet):
    """talk to vdr"""
    # pylint: disable=R0904
    # pylint finds too many public methods
    delimiter = '\r\n'

    def __init__(self):
        self.wrapper = None
        Telnet.__init__(self)

    def disableRemote(self, option):
        """disable a remote option"""

    def disableLocal(self, option):
        """disable a local option"""

    def lineReceived(self, line):
        """we got a full line from vdr"""
        if line.startswith('221 '):
            # this is an error because we should have
            # closed the connection ourselves after a
            # much shorter timeout than the server timeout
            LOGGER.error('vdr closes connection, timeout')
            self.wrapper.close()
            return
        if line.startswith('220 '):
            self.wrapper.openDeferred.callback(None)
            return
        if line.split(' ')[0] not in ['250', '354', '550']:
            LOGGER.error('from %s: %s' % (self.wrapper.name(), line))
        else:
            if 'r' in OPTIONS.debug:
                LOGGER.debug('from %s: %s' % (self.wrapper.name(), line))
        if self.wrapper.tasks.running:
            self.wrapper.tasks.gotAnswer(VdrMessage(line))
        else:
            LOGGER.error('vdr sent data without being asked:%s' % line)

class Vdr(Serializer):

    """talks to VDR. This is a wrapper around the Telnet protocol
    becaus we want to automatically close the connection after
    some timeout and automatically reopen it when needed. Vdr
    can only handle one client simultaneously."""

    # TODO: an event generator watching syslog for things like
    # switching channel
    eol = '\r\n'
    message = VdrMessage

    def __init__(self, hal, host='localhost', port=6419):
        Serializer.__init__(self, hal)
        self.host = host
        self.port = port
        self.protocol = None
        self.openDeferred = None
        self.prevChannel = None
        self.closeTimeout = 5

    def open(self):
        """open connection if not open"""
        def gotProtocol(result):
            """now we have a a connection, save it"""
            self.protocol = result
            self.protocol.wrapper = self
        if not self.protocol:
            LOGGER.debug('opening vdr')
            point = TCP4ClientEndpoint(reactor, self.host, self.port)
            factory = ClientFactory()
            factory.protocol = VdrProtocol
            point.connect(factory).addCallback(gotProtocol)
            self.openDeferred = Deferred()
            result = self.openDeferred
        else:
            result = succeed(None)
        reactor.callLater(5, self.close)
        return result

    def close(self):
        """close connection if open"""
        if self.protocol:
            if not (self.tasks.running or self.tasks.queued):
                if elapsedSince(self.tasks.allRequests[-1].sendTime) > self.closeTimeout - 1:
                    LOGGER.debug('closing vdr')
                    self.write('quit\n')
                    self.protocol.transport.loseConnection()
                    self.protocol = None

    def write(self, data):
        self.protocol.transport.write(data)

    def send(self, *args):
        """unconditionally send cmd"""
        _, msg = self.args2message(*args)
        return self.push(msg)

    def getChannel(self, dummyResult=None):
        """returns current channel number and name"""
        def got(result):
            """we got the current channel"""
            result = result.decoded.split(' ')
            if result[0] != '250':
                return None, None
            else:
                return result[1], ' '.join(result[2:])
        return self.push(self.message('chan')).addCallback(got)

    def gotoChannel(self, dummyResult, channel):
        """go to a channel if not yet there.
        Channel number and name are both accepted."""
        def got(result):
            """we got the current channel"""
            if channel not in result:
                self.prevChannel = result[0]
                return self.push(self.message('chan %s' % channel))
            else:
                return succeed(None)
        return self.getChannel().addCallback(got)
