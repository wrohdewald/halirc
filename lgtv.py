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

import sys, re, datetime, time

from lib import SerialDevice, OPTIONS, LOGGER, elapsedSince

class LGTV(SerialDevice):

    """Interface to probably most LG flatscreens"""
    def __init__(self, device='/dev/LGPlasma'):
        SerialDevice.__init__(self, device)
        self.commands = {
            'poweron': ('ka', '01'),
            'poweroff': ('ka', '00'),
            'muteon': ('ke', '00'),
            'muteoff': ('ke', '01'),
            'osdon': ('kl', '01'),
            'osdoff': ('kl', '00'),
            'aspect43': ('kc', '01'),
            'aspect169': ('kc', '02'),
            'aspectzoom': ('kc', '04'),
            'aspectoriginal': ('kc', '06'),
            'aspect149': ('kc', '07'),
            'aspectscan': ('kc', '09'),
            'aspectfullwidth': ('kc', '0B'),
            'inputdtv': ('xb', '00'),
            'inputanalog': ('xb', '10'),
            'inputav': ('xb', '20'),
            'inputcomponent1': ('xb', '40'),
            'inputcomponent2': ('xb', '41'),
            'inputhdmi1': ('xb', '90'),
            'inputhdmi2': ('xb', '91'),
            'mutescreenoff': ('kd', '00'),
            'mutescreenon': ('kd', '01'),
            'mutevideoon': ('kd', '10')
        }
        for zoom in range(1, 17):
            self.commands['aspectcinema%d' % zoom] = ('kc', '%02X' % (zoom + 15))

        for volume in range(0, 64):
            self.commands['volume%d' % volume] = ('kf', '%02X' % volume)
        self.videoMuted = None
        self.tvTimeout = 300
        self.setID = '01'

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
        self.send('poweron')
        self.send('volume0')
        self.send('aspectscan')
        self.send('mutescreenoff')

    def send(self, cmd):
        """if wanted value for cmd is not set in LGTV, set it.
        If we are acting on an event from the LG remote:
        the LG TV is quite fast in executing those events, so
        when we get here, the LG TV should already return the
        wanted value if it received the LG remote event too
        """
        command, value = self.parse(cmd)
        status = self.getAnswer(cmd)
        if status == value:
            return status
        if 's' in OPTIONS.debug:
            LOGGER.debug(
              'status is %s but we want %s' % (status, value))
        msg = ' '.join([command, self.setID, value])
        answer = self.communicate(msg + '\r', True, eol='x')
        if not re.match(r'.*OK', answer) and answer:
            msg = '%s %s: ERROR, we got:%s:' % (cmd, msg, answer)
            LOGGER.error(msg)
        return answer

    def parse(self, cmd):
        """translate the human readable commands into LG command sequences"""
        if not cmd in self.commands:
            LOGGER.critical('LGTV: unknown argument %s' % cmd)
            sys.exit(2)
        return self.commands[cmd]

    def getAnswer(self, cmd):
        """ask the LGTV for a value"""
        command, _ = self.parse(cmd)
        msg = ' '.join([command, self.setID, 'ff'])
        answer = self.communicate(msg + '\r', True, eol='x')
        match = re.match(r'.*OK(.*)$', answer)
        if 'r' in OPTIONS.debug:
            LOGGER.debug('LG.getAnswer:%s ' % answer)
        if match:
            return match.groups()[0]

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
