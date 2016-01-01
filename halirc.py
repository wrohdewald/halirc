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

import os, time

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
    def __init__(self, hal, vdr, yamaha, lgtv):
        self.vdr = vdr
        self.yamaha = yamaha
        self.lgtv = lgtv
        workdays = [0, 1, 2, 3, 4]

        hal.addTimer(self.kitchen, hour=4, minute=00, weekday=workdays)
        hal.addTimer(self.leaving, hour=4, minute=40, weekday=workdays)

    def kitchen(self):
        """start radio channel NDR 90,3 loudly"""
        self.yamaha.poweron().addCallback(
            self.yamaha.send, '@MAIN:INP=HDMI2').addCallback(
            self.yamaha.send, '@MAIN:VOL=-35.0')
        self.vdr.gotoChannel(None, 'NDR 90,3')
        self.lgtv.standby()

    def leaving(self):
        """off to train"""
        LOGGER.debug('morning.end')
        self.yamaha.standby(None)
        if self.vdr.prevChannel:
            self.vdr.gotoChannel(None, self.vdr.prevChannel)
        self.lgtv.standby(None)

def allOff(dummyEvent, devices):
    """as the name says. Will be called if the Yamaha is powered
    off - the LG does not make sense without Yamaha"""
    return DeferredList([x.standby() for x in devices])

class MyHal(Hal):
    """an example for user definitions"""

    def __init__(self):
        self.sxfeWatchFile = '/video0/nosxfe'
        # /usr/local/bin/sxfewatch watches for this file
        # and starts & stops the sxfe frontend accordingly.
        # vdr-sxfe uses the alsa device exclusively
        self.radioPreset = ''
        Hal.__init__(self)

    def desktopActive(self):
        """True if vdr-sxfe does not run"""
        return os.path.exists(self.sxfeWatchFile)

    def gotYamahaEvent(self, event, osdcat):
        """the Yamaha sent an event"""
        value = event.value()
        if event.humanCommand() == '@MAIN:VOL':
            value = '%.1f' % (float(value) + 80)
        if osdcat:
            return osdcat.write(value)
        else:
            return succeed(None)

    def desktop(self, dummyEvent, vdr):
        """toggle between desktop mode and vdr-sxfe"""
        os.system("chvt 7")
        if self.desktopActive():
            os.remove(self.sxfeWatchFile)
        else:
            with open(self.sxfeWatchFile,'w') as watchFd:
                watchFd.write('\n')
            vdr.send('hitk stop')
        return succeed(None)

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
            self.addRepeatableFilter(yamaha, cmd, self.gotYamahaEvent, osdcat)

        self.addRepeatableFilter(lirc, 'AcerP1165.PgUp', yamaha.mute)
        self.addFilter(lirc, 'AcerP1165.0', yamaha.send, '@MAIN:INP=HDMI2')
#        self.addFilter(lirc, 'AcerP1165.1', yamaha.send, 'SICD')
        self.addFilter(lirc, 'AcerP1165.2', yamaha.send, '@MAIN:INP=TUNER')
        self.addFilter(lirc, 'AcerP1165.3', yamaha.send, '@MAIN:INP=HDMI3')
#        self.addFilter(lirc, 'AcerP1165.4', yamaha.send, 'SIVDP')
#        self.addFilter(lirc, 'AcerP1165.5', yamaha.send, 'SIVCR-1')
#        self.addFilter(lirc, 'AcerP1165.6', yamaha.send, 'SIVCR-2')
#        self.addFilter(lirc, 'AcerP1165.7', yamaha.send, 'SIV.AUX')
#        self.addFilter(lirc, 'AcerP1165.8', yamaha.send, 'SICDR.TAPE')
#        self.addFilter(lirc, 'AcerP1165.9', yamaha.send, 'SITV')
        self.addFilter(lirc, 'AcerP1165.Left', yamaha.poweron)
        self.addFilter(lirc, 'AcerP1165.Right', allOff, [yamaha, lgtv, pioneer])
        self.addRepeatableFilter(lirc, 'AcerP1165.Down', yamaha.volume, 'Down')
        self.addRepeatableFilter(lirc, 'AcerP1165.Up', yamaha.volume, 'Up')

        for vdrKey in ('Ok', 'Channel+', 'Channel-', 'Menu', 'EPG', 'Info', 'Right',
            'Left', 'Up', 'Down', 'REC', 'Red', 'Green', 'Blue', 'Yellow',
            '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'):
            self.addFilter(lirc, 'Hauppauge6400.VDR' + vdrKey, lgtv.send, 'power:on')
            self.addFilter(lirc, 'Hauppauge6400.VDR' + vdrKey, lgtv.send, 'mutescreen:off')
        self.addFilter(lirc, 'Receiver12V.0', lgtv.standby)
        self.addFilter(lirc, 'Receiver12V.1', lgtv.poweron)
        self.addFilter(lirc, 'Hauppauge6400.Power2', lgtv.mutescreen, 'Power2', yamaha)
        self.addFilter(lirc, 'Receiver12V.2', lgtv.send, 'input:HDMI2')
        self.addFilter(lirc, 'Receiver12V.3', lgtv.send, 'input:HDMI2')
        self.addFilter(lirc, 'Receiver12V.4', lgtv.send, 'input:Component')
        self.addFilter(lirc, 'Receiver12V.5', lgtv.send, 'input:DTV')

        self.addFilter(lirc, 'Receiver12V.6', pioneer.poweron, yamaha)
        self.addFilter(lirc, 'Receiver12V.7', pioneer.standby)
        self.addFilter(lirc, 'XoroDVD.PlayPause', pioneer.play)
        self.addFilter(lirc, 'XoroDVD.Angle', pioneer.send, 'ST')
        self.addFilter(lirc, 'XoroDVD.Left', pioneer.send, '/A187FFFF/RU')
        self.addFilter(lirc, 'XoroDVD.Right', pioneer.send, '/A186FFFF/RU')
        self.addFilter(lirc, 'XoroDVD.Up', pioneer.send, '/A184FFFF/RU')
        self.addFilter(lirc, 'XoroDVD.Down', pioneer.send, '/A185FFFF/RU')
        self.addFilter(lirc, 'XoroDVD.Enter', pioneer.send, '/A181AFEF/RU')
        self.addFilter(lirc, 'XoroDVD.Forward', pioneer.send, 'NF')
        self.addFilter(lirc, 'XoroDVD.Rewind', pioneer.send, 'NR')
        self.addFilter(lirc, 'XoroDVD.FastForward', pioneer.send, '/A181AF3D/RU')
        self.addFilter(lirc, 'XoroDVD.FastRew', pioneer.send, '/A181AF3E/RU')
        self.addFilter(lirc, 'XoroDVD.Eject', pioneer.send, 'OP')

        self.addRepeatableFilter(lirc, 'AcerP1165.Zoom', self.desktop, vdr)
        self.addRepeatableFilter(lirc, 'AcerP1165.Source', lgtv.aspect, ('scan', '4:3', '14:9'))
        MorningAction(self, vdr, yamaha, lgtv)

# do not change this:
main(MyHal)
