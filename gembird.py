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

import os

from twisted.internet import reactor
from twisted.internet.protocol import ProcessProtocol

from lib import LOGGER, Message, Serializer

class GembirdProtocol(ProcessProtocol):
    # pylint: disable=W0232
    # pylint - we do not need __init__
    """we want to log gembird error messages"""

    def __init__(self, wrapper):
        self.wrapper = wrapper

    def outReceived(self, data):
        self.wrapper.lineReceived(data)

    def errReceived(self, data):
        """got stderr from sispmctl"""
        for line in data.split('\n'):
            if not line:
                # we do not want to log the copyright...
                # what sense does it make to add that
                # to an error message?
                break
            LOGGER.error('Gembird: %s' % line)

class GembirdMessage(Message):
    """the decoced, human readable form is:
        outlet1:on    switches on
        outlet1:off   switches off
        outlet1       asks for status
        instead of 1 you can also use 2,3,4
    """
    commands = {}
    for _ in ('1', '2', '3', '4', 'all'):
        commands['on%s' % _] = '-o %s' % _
        commands['off%s' % _] = '-f %s' % _
        commands['on%s' % _] = '-o %s' % _

    def __init__(self, decoded=None, encoded=None):
        self.outlet = None
        Message.__init__(self, decoded, encoded)

    def _setAttributes(self, decoded, encoded):
        if encoded is not None:
            if encoded == '': # timeout
                return
            parts = encoded.split('\n')[1].split() # the first line says 'accessing...'
            self.outlet = parts[-2][0]
            assert self.outlet in '1234', encoded
            assert parts[-1] in ('on', 'off'), encoded
            if parts[-1] == 'on':
                self._encoded = '-o %s' % self.outlet
                self._decoded = 'outlet%s:on'
            else:
                self._encoded = '-f %s' % self.outlet
                self._decoded = 'outlet%s:off'
        else: # decoded
            assert decoded.startswith('outlet'), decoded
            self.outlet = decoded[6]
            assert self.outlet in '1234', decoded
            self._decoded = decoded
            if ':' in decoded:
                _, which = decoded.split(':')
                assert which in ('on', 'off'), decoded
                if which == 'on':
                    self._encoded = '-o %s' % self.outlet
                else:
                    self._encoded = '-f %s' % self.outlet
            else:
                self._encoded = '-g %s' % self.outlet
                self.isQuestion = True

    def humanCommand(self):
        if self.outlet is None:
            return ''
        return 'outlet%s' % self.outlet

    def value(self):
        """the value in human format"""
        if self.outlet is None:
            return ''
        parts = self._decoded.split(':')
        if len(parts) > 1:
            return parts[1]
        else:
            return None

    def command(self):
        """the command of this message, encoded"""
        if self.outlet is None:
            return ''
        return self._encoded.split()[0]

class GembirdOutlet:
    """we want one object per outlet"""
    def __init__(self, gembird, outlet):
        self.gembird = gembird
        self.outlet = outlet # just in case they are cascaded

    def delay(self, previous, this):
        """compute necessary delay before we can execute request"""
        return self.gembird.delay(previous, this)

    def poweron(self, *dummyArgs):
        """switch power on"""
        return self.gembird.poweron(self.outlet)

    def standby(self, *dummyArgs):
        """switch power off"""
        return self.gembird.standby(self.outlet)

class Gembird(Serializer):
    """use the external program sispmctl for controlling
    the Gembird USB power outlet"""

    message = GembirdMessage

    def __init__(self, hal, device='/dev/steckerleiste', outlets=4):
        Serializer.__init__(self, hal)
        self.device = device
        self.outlets = list(GembirdOutlet(self, x) for x in range(1, outlets + 1))

    def __getitem__(self, index):
        """directly index the outlets. Range is 1..x because that is
        how they are numbered by Gembird"""
        return self.outlets[index - 1] # but the array is 0..x-1

    @staticmethod
    def delay(previous, dummyThis):
        """compute necessary delay before we can execute request"""
        if not previous.message.isQuestion:
            # the gembird needs this time for switching, otherwise it returns an error
            return 0.7
        return 0

    def lineReceived(self, data):
        """nothing special here"""
        Serializer.defaultInputHandler(self, data)

    def write(self, data):
        """write to the osd_cat process"""
        sisargs = ['sispmctl', '-d', self.device]
        sisargs.append(data)
        reactor.spawnProcess(GembirdProtocol(self), 'sispmctl', args=sisargs, env={'PATH': os.environ['PATH']})

