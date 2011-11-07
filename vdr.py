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

from lib import Device, OPTIONS, LOGGER
from telnetlib import Telnet

class VDRServer(Device):
    """talks to VDR"""
    def __init__(self, name='VDR', host='localhost', port=6419):
        Device.__init__(self, name)
        self.host = host
        self.port = port
        self.telnet = None

    def open(self):
        """open connection if not open"""
        if not self.telnet:
            if not Device.open(self):
                return False
            self.telnet = Telnet()
            self.telnet.open(self.host, self.port, timeout=10)
            greeting = self.telnet.read_until('\n', timeout=2)[:-1]
            if not greeting:
                greeting = 'VDR server is probably in use'
            if greeting.split(' ')[0] != '220':
                LOGGER.error('VDR server is not ready: %s' % greeting)
                self.close(openFailed=True)
                return False
        return True

    def close(self, openFailed=False):
        """close connection if open"""
        if self.telnet:
            self.telnet.write('quit\n')
            self.telnet.close()
            self.telnet = None
        Device.close(self, openFailed)

    def send(self, cmd):
        """send data to VDR"""
        if not self.open():
            return ''
        if cmd[-1] != '\n':
            cmd = cmd + '\n'
        if 's' in OPTIONS.debug:
            LOGGER.debug('sending to %s: %s' % (type(self).__name__, cmd[:-1]))
        self.telnet.write(cmd)
        result = self.telnet.read_until('\n')[:-2] # line end is CRLF
        if result.split(' ')[0] not in ['250', '354', '550']:
            LOGGER.error('from %s: %s' % (type(self).__name__, result))
        else:
            if 'r' in OPTIONS.debug:
                LOGGER.debug('from %s: %s' % (type(self).__name__, result))
        return result

    def getChannel(self,):
        """returns current channel number and name"""
        answer = self.send('chan').split(' ')
        if answer[0] != '250':
            return 'nochannelfound'
        return answer[1], ' '.join(answer[2:])

    def gotoChannel(self, channel):
        """go to a channel if not yet there.
        Channel number and name are both accepted."""
        if channel not in self.getChannel():
            self.send('chan %s' % channel)
