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
from denon import Denon
from vdr import Vdr
from pioneer import Pioneer

class MorningAction(object):
    """very custom..."""
    def __init__(self, hal, vdr, denon, lgtv):
        self.vdr = vdr
        self.denon = denon
        self.lgtv = lgtv
        self.silencer = '/home/wr/ausschlafen'
        workdays = [0, 1, 2, 3, 4]

        hal.addTimer(self.start, hour=3, minute=56, weekday=workdays)
        hal.addTimer(self.changeVolume, hour=4, minute=20, weekday=workdays)
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

def allOff(dummyEvent, denon, lgtv, gembirdOutlet):
    """as the name says. Will be called if the Denon is powered
    off - the LG does not make sense without Denon"""
    return DeferredList([
        denon.standby(),
        lgtv.standby(),
        gembirdOutlet.standby()])

class MyHal(Hal):
    """an example for user definitions"""

    def __init__(self):
        self.sxfeWatchFile = '/video0/nosxfe'
        # /usr/local/bin/sxfewatch watches for this file
        # and starts & stops the sxfe frontend accordingly.
        # vdr-sxfe uses the alsa device exclusively
        self.osdCatEnabled = True # self.desktopActive()
        # it would be nice to see changes of volume or sound encoding
        # on the TV but this makes vdpau crash with HD material
        self.radioPreset = ''
        Hal.__init__(self)

    def desktopActive(self):
        """True if vdr-sxfe does not run"""
        return os.path.exists(self.sxfeWatchFile)

    def gotDenonEvent(self, event, osdcat):
        """the Denon sent an event"""
        if not self.osdCatEnabled:
            return succeed(None)
        value = event.value()
        if event.humanCommand() == 'MV':
            if len(value) == 3:
                value = value[:2] + '.5'
        elif event.humanCommand() == 'TP':
            self.radioPreset = 'Speicher ' + value
            # after TP, the Denon always sends TF
            return succeed(None)
        elif event.humanCommand() == 'TF':
            if value >= '050000':
                value = '%s AM %d kHz' % (self.radioPreset, int(value[:4]))
            else:
                value = '%s FM %d.%s MHz' % (self.radioPreset, int(value[:4]), value[4:])
            self.radioPreset = ''
        if osdcat:
            return osdcat.write(value)
        else:
            return succeed(None)

    def desktop(self, dummyEvent, vdr):
        """toggle between desktop mode and vdr-sxfe"""
        os.system("chvt 7")
        if self.desktopActive():
            self.osdCatEnabled = True
            os.remove(self.sxfeWatchFile)
        else:
            self.osdCatEnabled = True
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
        denon = Denon(self)
        denon.answersAsEvents = True
        vdr = Vdr(self)
        lgtv = LGTV(self)
        osdcat = OsdCat()
        gembird = Gembird(self)
        pioneer = Pioneer(self, host='blueray')
        for cmd in ('MV', 'SI', 'MS', 'TF', 'TP'):
            self.addRepeatableFilter(denon, cmd, self.gotDenonEvent, osdcat)
        self.addRepeatableFilter(lirc, 'AcerP1165.PgUp', denon.mute)
        self.addFilter(lirc, 'AcerP1165.PgDown', denon.queryStatus)
        self.addFilter(lirc, 'AcerP1165.0', denon.send, 'SIDBS/SAT')
        self.addFilter(lirc, 'AcerP1165.1', denon.send, 'SICD')
        self.addFilter(lirc, 'AcerP1165.2', denon.send, 'SITUNER')
        self.addFilter(lirc, 'AcerP1165.3', denon.send, 'SIDVD')
        self.addFilter(lirc, 'AcerP1165.4', denon.send, 'SIVDP')
        self.addFilter(lirc, 'AcerP1165.5', denon.send, 'SIVCR-1')
        self.addFilter(lirc, 'AcerP1165.6', denon.send, 'SIVCR-2')
        self.addFilter(lirc, 'AcerP1165.7', denon.send, 'SIV.AUX')
        self.addFilter(lirc, 'AcerP1165.8', denon.send, 'SICDR.TAPE')
        self.addFilter(lirc, 'AcerP1165.9', denon.send, 'SITV')
        self.addFilter(lirc, 'AcerP1165.Left', denon.poweron)
        self.addFilter(lirc, 'AcerP1165.Right', allOff, denon, lgtv, gembird[3])
        self.addRepeatableFilter(lirc, 'AcerP1165.Down', denon.volume, 'DOWN')
        self.addRepeatableFilter(lirc, 'AcerP1165.Up', denon.volume, 'UP')

        self.addFilter(lirc, 'Receiver12V.0', lgtv.standby)
        self.addFilter(lirc, 'Receiver12V.1', lgtv.send, 'power:on')
        self.addRepeatableFilter(lirc, 'Hauppauge6400', lgtv.mutescreen, 'Power2', denon)
        self.addRepeatableFilter(lirc, 'AcerP1165.Zoom', lgtv.mutescreen, 'Power2', denon)
        self.addFilter(lirc, 'Receiver12V.6', lgtv.mutescreen, 'Power2', denon)
        self.addFilter(lirc, 'Receiver12V.2', lgtv.send, 'input:HDMI1')
        self.addFilter(lirc, 'Receiver12V.3', lgtv.send, 'input:HDMI2')
        self.addFilter(lirc, 'Receiver12V.4', lgtv.send, 'input:Component')
        self.addFilter(lirc, 'Receiver12V.5', lgtv.send, 'input:DTV')

        self.addFilter(lirc, 'XoroDVD.PlayPause', pioneer.send, 'PL')
        self.addFilter(lirc, 'Receiver12V.6', pioneer.poweron, gembirdOutlet=gembird[3])
        self.addFilter(lirc, 'Receiver12V.7', pioneer.standby, gembirdOutlet=gembird[3])
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


        self.addRepeatableFilter(lirc, 'AcerP1165.Zoom', self.desktop, vdr)
        self.addRepeatableFilter(lirc, 'AcerP1165.Source', lgtv.aspect, ('scan', '4:3', '14:9'))
        self.addRepeatableFilter(lirc, 'AcerP1165.Freeze', denon.surround, self.osdCatEnabled,
            # depending on the source encoding, the actual setting may not
            # always be what this list says
            (
              ('PSMODE:CINEMA'),
              ('PSMODE:MUSIC'),
              ('MS5CH STEREO'),
              ('MSCLASSIC CONCERT'),
              ('MSPURE DIRECT'),
              ('MSWIDE SCREEN'),
              ('MSSUPER STADIUM'),
              ('MSROCK ARENA'),
              ('MSJAZZ CLUB'),
              ('MSMONO MOVIE'),
              ('MSDTS NEO:6')
            ))

        MorningAction(self, vdr, denon, lgtv)

# do not change this:
main(MyHal)
