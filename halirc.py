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

Everything in halirc.py is just an example. You want to start
your own myhalirc.py and do there whatever you want.
"""

import os

from lib import LOGGER, Hal, main
from lgtv import LGTV
from denon import Denon
from vdr import Vdr

class MorningAction(object):
    """very custom..."""
    def __init__(self, hal, vdr, denon, lgtv):
        self.vdr = vdr
        self.denon = denon
        self.lgtv = lgtv
        self.silencer = '/home/wr/ausschlafen'
        workdays = [0, 1, 2, 3, 4]

        hal.addTimer(self.start, hour=3, minute=58, weekday=workdays)
        hal.addTimer(self.changeVolume, hour=4, minute=21, weekday=workdays)
        hal.addTimer(self.end, hour=4, minute=40, weekday=workdays)

    def wanted(self):
        """do we actually want to be triggered?"""
        if os.path.exists(self.silencer):
            return False
        return True

    def start(self):
        """start channel NDR 90,3 loudly"""
        LOGGER.debug('morning.start')
        if self.wanted():
            self.denon.poweron().addCallback(
                self.denon.send, 'SIDBS/SAT').addCallback(
                self.denon.send, 'MV60')
            self.vdr.gotoChannel(None, 'NDR 90,3')
            self.lgtv.standby()

    def changeVolume(self):
        """kitchen time"""
        LOGGER.debug('morning.changeVolume')
        if self.wanted():
            self.denon.send('MV42')

    def end(self):
        """off to train"""
        LOGGER.debug('morning.end')
        if self.wanted():
            self.denon.standby(None)
            if self.vdr.prevChannel:
                self.vdr.gotoChannel(None, self.vdr.prevChannel)
            self.lgtv.standby(None)
        elif os.path.exists(self.silencer):
            os.remove(self.silencer)

class MyHal(Hal):
    """an example for user definitions"""

    def allOff(self, event, denon, lgtv):
        """as the name says. Will be called if the Denon is powered
        off - the LG does not make sense without Denon"""
        denon.standby()
        lgtv.standby()

    def setup(self):
        """
        my own setup.
        since I do not want three receiving IRs, I send all commands to the
        IR receiver connected with lirc, and here we pass them on to the
        correct device. I do not use the original remote codes because I do not
        want the devices to get the same command simultaneously from the remote
        and from halirc. AcerP1165, Hauppauge6400 and Receiver12V are just some
        remote controls I do not need otherwise.
        """
        denon = Denon(self)
        vdr = Vdr(self)
        lgtv = LGTV(self)
        self.addRemoteFilter('AcerP1165', 'PgUp', denon.mute)
        self.addRemoteFilter('AcerP1165', 'PgDown', denon.queryStatus)
        self.addRemoteFilter('AcerP1165', '0', denon.send, args='SIDBS/SAT')
        self.addRemoteFilter('AcerP1165', '1', denon.send, args='SICD')
        self.addRemoteFilter('AcerP1165', '2', denon.send, args='SITUNER')
        self.addRemoteFilter('AcerP1165', '3', denon.send, args='SIDVD')
        self.addRemoteFilter('AcerP1165', '4', denon.send, args='SIVDP')
        self.addRemoteFilter('AcerP1165', '5', denon.send, args='SIVCR-1')
        self.addRemoteFilter('AcerP1165', '6', denon.send, args='SIVCR-2')
        self.addRemoteFilter('AcerP1165', '7', denon.send, args='SIV.AUX')
        self.addRemoteFilter('AcerP1165', '8', denon.send, args='SICDR.TAPE')
        self.addRemoteFilter('AcerP1165', '9', denon.send, args='SITV')
        self.addRemoteFilter('AcerP1165', 'Left', denon.poweron)
        self.addRemoteFilter('AcerP1165', 'Right', self.allOff, args=[denon, lgtv])
        self.addRemoteFilter('AcerP1165', 'Down', denon.volume, args='DOWN')
        self.addRemoteFilter('AcerP1165', 'Up', denon.volume, args='UP')

        self.addRemoteFilter('Receiver12V', '0', lgtv.standby)
        self.addRemoteFilter('Receiver12V', '1', lgtv.send, args='power:on')
        self.addRemoteFilter('Hauppauge6400', None, lgtv.mutescreen, args='Power2')
        self.addRemoteFilter('Receiver12V', '2', lgtv.send, args='input:HDMI1')
        self.addRemoteFilter('Receiver12V', '3', lgtv.send, args='input:HDMI2')
        self.addRemoteFilter('Receiver12V', '4', lgtv.send, args='input:component')
        self.addRemoteFilter('Receiver12V', '5', lgtv.send, args='input:DTV')

        MorningAction(self, vdr, denon, lgtv)

# do not change this:
main(MyHal)
