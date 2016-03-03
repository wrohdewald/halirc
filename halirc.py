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

from twisted.internet.defer import succeed, DeferredList

from lib import LOGGER, Hal, main, OsdCat
from lirc import Lirc
from gembird import Gembird
from lgtv import LGTV
from yamaha import Yamaha
from vdr import Vdr
from pioneer import Pioneer

class MorningAction(object):
    """very custom..."""
    def __init__(self, hal, vdr, yamaha):
        self.vdr = vdr
        self.yamaha = yamaha
        workdays = [0, 1, 2, 3, 4]

        hal.addTimer(self.kitchen, hour=3, minute=50, weekday=workdays)
        hal.addTimer(self.leaving, hour=4, minute=35, weekday=workdays)

    def kitchen(self):
        """start radio channel NDR 90,3 loudly"""
        self.yamaha.poweron().addCallback(
            self.yamaha.send, '@MAIN:INP=HDMI2').addCallback(
            self.yamaha.send, '@MAIN:VOL=-35.0')
        self.vdr.gotoChannel(None, 'NDR 90,3')

    def leaving(self):
        """off to train"""
        LOGGER.debug('morning.end')
        self.yamaha.standby(None)
        if self.vdr.prevChannel:
            self.vdr.gotoChannel(None, self.vdr.prevChannel)

def allOff(dummyEvent, devices):
    """as the name says. Will be called if the Yamaha is powered
    off - the LG does not make sense without Yamaha"""
    return DeferredList([x.standby() for x in devices])

class MyHal(Hal):
    """an example for user definitions"""

    def __init__(self):
        self.radioPreset = ''
        self.yamaha = None
        Hal.__init__(self)

    @staticmethod
    def gotYamahaEvent(event, osdcat):
        """the Yamaha sent an event"""
        value = event.value()
        if event.humanCommand() == '@MAIN:VOL':
            value = '%.1f' % (float(value) + 80)
        if osdcat:
            return osdcat.write(value)
        else:
            return succeed(None)

    @staticmethod
    def kodi(event, vdr):
        """toggle between kodi and vdr"""
        os.system("chvt 7")
        return vdr.toggleSofthddevice(event)

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
        # pylint: disable=R0915
        lirc = Lirc(self)
        yamaha = Yamaha(self, host='yamaha')
        vdr = Vdr(self)
        lgtv = LGTV(self)
        osdcat = OsdCat()
        gembird = Gembird(self)
        pioneer = Pioneer(self, host='pioneer', outlet=gembird[3])
        self.yamaha = yamaha
        for cmd in ('@MAIN:VOL', ):
            self.addRepeatableTrigger(yamaha, cmd, self.gotYamahaEvent, osdcat)

        self.addRepeatableTrigger(lirc, 'AcerP1165.PgUp', yamaha.mute)
        self.addTrigger(lirc, 'AcerP1165.0', yamaha.send, '@MAIN:INP=HDMI2')
#        self.addTrigger(lirc, 'AcerP1165.1', yamaha.send, 'SICD')
        self.addTrigger(lirc, 'AcerP1165.2', yamaha.send, '@MAIN:INP=TUNER')
        self.addTrigger(lirc, 'AcerP1165.3', yamaha.send, '@MAIN:INP=HDMI3')
#        self.addTrigger(lirc, 'AcerP1165.4', yamaha.send, 'SIVDP')
#        self.addTrigger(lirc, 'AcerP1165.5', yamaha.send, 'SIVCR-1')
#        self.addTrigger(lirc, 'AcerP1165.6', yamaha.send, 'SIVCR-2')
#        self.addTrigger(lirc, 'AcerP1165.7', yamaha.send, 'SIV.AUX')
#        self.addTrigger(lirc, 'AcerP1165.8', yamaha.send, 'SICDR.TAPE')
#        self.addTrigger(lirc, 'AcerP1165.9', yamaha.send, 'SITV')

# Das hier waere nett, aber der Yamaha reagiert da schon selber drauf
# TODO: mit debug=all kommen nicht mehr alle lirc Events ins Debuglog
        self.addTrigger(lirc, 'Denon_AVR2805.Channel+', yamaha.send, '@TUN:PRESET=Up')
        self.addTrigger(lirc, 'Denon_AVR2805.Channel-', yamaha.send, '@TUN:PRESET=Down')
        self.addTrigger(lirc, 'Denon_AVR2805.Tuning+', yamaha.send, '@TUN:FMFREQ=Auto Up')
        self.addTrigger(lirc, 'Denon_AVR2805.Tuning-', yamaha.send, '@TUN:FMFREQ=Auto Down')
        self.addTrigger(lirc, 'AcerP1165.Left', yamaha.poweron)
        self.addTrigger(lirc, 'AcerP1165.Right', allOff, [yamaha, lgtv, pioneer])
        self.addRepeatableTrigger(lirc, 'AcerP1165.Down', yamaha.volume, 'Down')
        self.addRepeatableTrigger(lirc, 'AcerP1165.Up', yamaha.volume, 'Up')

        for vdrKey in ('Ok', 'Channel+', 'Channel-', 'Menu', 'EPG', 'Info', 'Right',
            'Left', 'Up', 'Down', 'REC', 'Red', 'Green', 'Blue', 'Yellow',
            '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'):
            self.addTrigger(lirc, 'Hauppauge6400.VDR' + vdrKey, lgtv.send, 'power:on')
            self.addTrigger(lirc, 'Hauppauge6400.VDR' + vdrKey, lgtv.send, 'mutescreen:off')
        self.addTrigger(lirc, 'Receiver12V.0', lgtv.standby)
        self.addTrigger(lirc, 'Receiver12V.1', lgtv.poweron)
        self.addTrigger(lirc, 'Hauppauge6400.Power2', lgtv.mutescreen, 'Power2', yamaha)
        self.addTrigger(lirc, 'Receiver12V.2', lgtv.send, 'input:HDMI2')
        self.addTrigger(lirc, 'Receiver12V.3', lgtv.send, 'input:HDMI2')
        self.addTrigger(lirc, 'Receiver12V.4', lgtv.send, 'input:Component')
        self.addTrigger(lirc, 'Receiver12V.5', lgtv.send, 'input:DTV')

        self.addTrigger(lirc, 'Receiver12V.6', pioneer.poweron, yamaha)
        self.addTrigger(lirc, 'Receiver12V.7', pioneer.standby)
        self.addTrigger(lirc, 'XoroDVD.PlayPause', pioneer.play)
        self.addTrigger(lirc, 'XoroDVD.Angle', pioneer.send, 'ST')
        self.addTrigger(lirc, 'XoroDVD.Left', pioneer.send, '/A187FFFF/RU')
        self.addTrigger(lirc, 'XoroDVD.Right', pioneer.send, '/A186FFFF/RU')
        self.addTrigger(lirc, 'XoroDVD.Up', pioneer.send, '/A184FFFF/RU')
        self.addTrigger(lirc, 'XoroDVD.Down', pioneer.send, '/A185FFFF/RU')
        self.addTrigger(lirc, 'XoroDVD.Enter', pioneer.send, '/A181AFEF/RU')
        self.addTrigger(lirc, 'XoroDVD.Forward', pioneer.send, 'NF')
        self.addTrigger(lirc, 'XoroDVD.Rewind', pioneer.send, 'NR')
        self.addTrigger(lirc, 'XoroDVD.FastForward', pioneer.send, '/A181AF3D/RU')
        self.addTrigger(lirc, 'XoroDVD.FastRew', pioneer.send, '/A181AF3E/RU')
        self.addTrigger(lirc, 'XoroDVD.Eject', pioneer.send, 'OP')

        self.addRepeatableTrigger(lirc, 'AcerP1165.Zoom', self.kodi, vdr)
        self.addRepeatableTrigger(lirc, 'AcerP1165.Source', lgtv.aspect, ('scan', '4:3', '14:9'))
        MorningAction(self, vdr, yamaha)

# do not change this:
main(MyHal)
