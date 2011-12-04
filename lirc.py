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
from twisted.internet import reactor
from twisted.internet.endpoints import UNIXClientEndpoint
from twisted.internet.protocol import ClientFactory

from lib import Message, Serializer

class LircMessage(Message):
    """holds contents received from a remote control or sent with
       an IR sender. 
       The encoded format is as read from the lircd socket but
       without the first part with the raw code.
       The decoded format is remote.button.repeat where button
       and repeat can be omitted. Omitted parts match any value.
       If a part includes a dot, that part must be surrounded by
       double quotes. Double quotes are not allowed in a part.
       Allowed examples:
       AcerP1165.Up.00
       AcerP1165
       "My other remote".button
    """

    def __init__(self, decoded=None, encoded=None):
        self.raw = None
        self.repeat = None
        self.button = None
        self.remote = None
        Message.__init__(self, decoded, encoded)

    def decodedParts(self, decoded=None):
        """a generator returning the single parts without their optional quotes"""
        rest = decoded or self._decoded
        wantedParts = 3
        while len(rest):
            if rest[0] == '"':
                quote2 = rest.index('"', 1)
                yield rest[1:quote2]
                wantedParts -= 1
                rest = rest[quote2:]
                if len(rest):
                    assert(rest[0] == '.'), decoded
                    rest = rest[1:]
            else:
                if '.' in rest:
                    parts = rest.split('.')
                    yield parts[0]
                    wantedParts -= 1
                    rest = '.'.join(parts[1:])
                else:
                    yield rest
                    wantedParts -= 1
                    rest = ''
        while wantedParts:
            yield ''
            wantedParts -= 1       

    def _setAttributes(self, decoded, encoded):
        """initialize all internal values"""
        if encoded is not None:
            assert '"' not in encoded, encoded
            self.raw, self.repeat, self.button, self.remote = encoded.split(' ')
        else: # decoded
            self._decoded = decoded
            self.remote, self.button, self.repeat = self.decodedParts(decoded)
        parts = [self.remote, self.button, self.repeat]
        self._decoded = '.'.join('"%s"' % x if '.' in x else x for x in parts)
        self._encoded = ' '.join([self.repeat or '', self.button or '', self.remote])

    def humanCommand(self):
        return self.decoded

    def __str__(self):
        return self._decoded

    def __eq__(self, other):
        """are those messages equal?"""
        if type(self) != type(other):
            return False
        myParts = list(self.decodedParts())
        otherParts = list(other.decodedParts())
        for idx in range(0, min([len(myParts), len(otherParts)])):
            myPart, otherPart = myParts[idx], otherParts[idx]
            if myPart and otherPart and myPart != otherPart:
                return False
        return True

class LircProtocol(LineOnlyReceiver):
    """the protocol for receiving lines from the lircd socket"""
    delimiter = '\n'

    def __init__(self):
        self.wrapper = None

    def lineReceived(self, data):
        """we got a raw line from the lirc socket"""
        msg = self.wrapper.message(encoded=data)
        self.wrapper.hal.eventReceived(msg)

class Lirc(Serializer):
    """for now can only receive events from lirc"""
    message = LircMessage

    def __init__(self, hal, device='/var/run/lirc/lircd'):
        """the default lirc socket to listen on is /var/run/lirc/lircd.
        Change that by setting self.irwSocket in setup()"""
        Serializer.__init__(self, hal)
        self.irwSocket = device
        self.protocol = None
        self.open()

    def open(self):
        """open the connection to the lircd socket"""
        def gotProtocol(result):
            """now we have a connection"""
            self.protocol = result
            self.protocol.wrapper = self
        point = UNIXClientEndpoint(reactor, self.irwSocket, timeout=2)
        factory = ClientFactory()
        factory.protocol = LircProtocol
        point.connect(factory).addCallback(gotProtocol)

    def write(self, data):
        """write to lirc not yet implemented"""
        raise Exception('writing to lirc is not yet implemented')

    def send(self, *args):
        """write to lirc not yet implemented"""
        raise Exception('writing to lirc is not yet implemented')

