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

import sys, re, datetime

from lib import SerialDevice, OPTIONS, LOGGER, elapsedSince

class LGTV(SerialDevice):

    """Interface to probably most LG flatscreens"""
    def __init__(self, device='/dev/LGPlasma'):
        SerialDevice.__init__(self, device)
        self.commands = {
            'poweron':'ka 00 01',
            'poweroff': 'ka 00 00',
            'muteon': 'ke 00 00',
            'muteoff': 'ke 00 01',
            'osdon': 'kl 00 01',
            'osdoff': 'kl 00 00',
            'aspect43': 'kc 00 01',
            'aspect169': 'kc 00 02',
            'aspectzoom': 'kc 00 04',
            'aspectoriginal': 'kc 00 06',
            'aspect149': 'kc 00 07',
            'aspectscan': 'kc 00 09',
            'aspectfullwidth': 'kc 00 0B',
            'inputdtv': 'xb 00 00',
            'inputanalog': 'xb 00 10',
            'inputav': 'xb 00 20',
            'inputcomponent': 'xb 00 40',
            'inputhdmi1': 'xb 00 70',
            'inputhdmi2': 'xb 00 71',
            'mutescreenoff': 'kd 00 00',
            'mutescreenon': 'kd 00 01',
            'mutevideoon': 'kd 00 10'
        }
        for zoom in range(1, 17):
            self.commands['aspectcinema%d' % zoom] = 'kc 00 %02X' % (zoom + 15)

        for volume in range(0, 64):
            self.commands['volume%d' % volume] = 'kf 00 %02X' % volume
        self.videoMuted = None
        self.tvTimeout = 30

    def setEvent(self, event):
        """event is the next event to be processed or None if there
        was no event while timeout"""
        if not event:
            if self.tvTimeout-0.5 < elapsedSince(self.videoMuted) < self.tvTimeout+0.5:
                self.standby()
                self.close() # manual close because standby starts close timer again
        SerialDevice.setEvent(self, event)

    def init(self):
        """init the LGTV for VDR usage"""
        self.sendIfNot('01', 'poweron')
        self.sendIfNot('00', 'muteon')
        self.sendIfNot('00', 'volume0')
        self.sendIfNot('09', 'aspectscan')
        self.sendIfNot('00', 'mutescreenoff')

    def sendIfNot(self, wantedStatus, cmd):
        """if wantedStatus for cmd is not set in LGTV, set it"""
        status = self.getAnswer(cmd)
        if status != wantedStatus:
            if 's' in OPTIONS.debug:
                LOGGER.debug(
                  'status is %s but we want %s' % (status, wantedStatus))
            return self.send(cmd)

    def parse(self, cmd):
        """translate the human readable commands into LG command sequences"""
        if not cmd in self.commands:
            LOGGER.critical('LGTV: unknown argument %s' % cmd)
            sys.exit(2)
        return self.commands[cmd]

    def getAnswer(self, cmd):
        """ask the LGTV for a value"""
        command = self.parse(cmd)
        command = command[:6] + 'ff'
        for _ in range(0, 5):
            answer = self.communicate(command + '\r', True, eol='x')
            match = re.match(r'.*OK(.*)$', answer)
            if match:
                break
        if 'r' in OPTIONS.debug:
            LOGGER.debug('LG.getAnswer:%s ' % answer)
        if match:
            return match.groups()[0]

    def send(self, cmd):
        """send cmd to LGTV and return the answer"""
        command = self.parse(cmd)
        answer = self.communicate(command + '\r', True, eol='x')
        if not re.match(r'.*OK', answer):
            msg = '%s %s: ERROR %s' % (cmd, command, answer)
            LOGGER.error(msg)
        return answer

    def isPoweredOn(self):
        """is the LGTV on?"""
        return self.getAnswer('poweron') != '00'

    def standby(self):
        """power off the LGTV"""
        if self.isPoweredOn():
            self.send('poweroff')

    def mutevideo(self, muteButton):
        """except for muteButton, all remote buttons make video visible again"""
        if self.event.button == muteButton:
            self.videoMuted = datetime.datetime.now()
            self.send('mutevideoon')
        else:
            if self.getAnswer('mutevideoon') != '00':
                LOGGER.debug('unmuting')
                self.videoMuted = None
                self.init()
