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


TODO
Harmony: LG alles!!!!!! separate Codes vom Receiver12V anlernen
Harmony: Denon Lautstaerke und Mute dito
"""

import os

from lib import parseOptions, initLogger
parseOptions()
initLogger()
from lib import Irw, Worker, LOGGER
from denon import Denon
from lgtv import LGTV
from vdr import VDRServer

class MorningAction(object):
    """very custom..."""
    def __init__(self, worker, vdr, denon, lgtv):
        self.vdr = vdr
        self.denon = denon
        self.lgtv = lgtv
        self.prevChannel = None
        self.silencer = '/home/wr/ausschlafen'
        workdays = [0, 1, 2, 3, 4, 5, 6]
        worker.addTimer(self.start, hour=3, minute=58, weekday=workdays)
        worker.addTimer(self.changeVolume, hour=4, minute=21, weekday=workdays)
        worker.addTimer(self.end, hour=4, minute=40, weekday=workdays)

    def wanted(self):
        """do we actually want to be triggered?"""
        if os.path.exists(self.silencer):
            return False
        return True

    def start(self):
        """start channel 93 loudly"""
        LOGGER.debug('morning.start')
        if self.wanted():
            self.denon.init()
            self.denon.sendIfNot('SIDBS/SAT')
            self.denon.sendIfNot('MV57')
            self.prevChannel = self.vdr.getChannel()
            if self.prevChannel != 'NDR 90,3':
                self.vdr.gotoChannel('NDR 90,3')
            else:
                self.prevChannel = None
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
            self.denon.standby()
            if self.prevChannel:
                self.vdr.gotoChannel(self.prevChannel)
            self.lgtv.standby()
        elif os.path.exists(self.silencer):
            os.remove(self.silencer)

def main():
    """define main, avoid to pollute global namespace"""
    # pylint: disable=W0603
    print 'main:', LOGGER
    irw = Irw()
    denon = Denon()
    lgPlasma = LGTV()
    vdr = VDRServer()
    appliances = [denon, lgPlasma, vdr]

    worker = Worker()

    # use buttons of an unused remote for controlling
    # the Denon because the IR receiver of the Denon
    # is too far away
    worker.addFilter(denon.volume, args='UP', remote='AcerP1165', button='Up', repeat=None)
    worker.addFilter(denon.volume, args='DOWN', remote='AcerP1165', button='Down', repeat=None)
    worker.addFilter(denon.mute, remote='AcerP1165', button='PgUp')
    worker.addFilter(denon.queryStatus, remote='AcerP1165', button='PgDown')
    worker.addFilter(denon.send, args='SIDBS/SAT', remote='AcerP1165', button='0')
    worker.addFilter(denon.send, args='SICD', remote='AcerP1165', button='1')
    worker.addFilter(denon.send, args='SITUNER', remote='AcerP1165', button='2')
    worker.addFilter(denon.send, args='SIDVD', remote='AcerP1165', button='3')
    worker.addFilter(denon.send, args='SIVDP', remote='AcerP1165', button='4')
    worker.addFilter(denon.send, args='SIVCR-1', remote='AcerP1165', button='5')
    worker.addFilter(denon.send, args='SIVCR-2', remote='AcerP1165', button='6')
    worker.addFilter(denon.send, args='SIV.AUX', remote='AcerP1165', button='7')
    worker.addFilter(denon.send, args='SICDR.TAPE', remote='AcerP1165', button='8')
    worker.addFilter(denon.send, args='SITV', remote='AcerP1165', button='9')
    worker.addFilter(denon.send, args='PWON', remote='AcerP1165', button='Left')
    worker.addFilter(denon.send, args='PWSTANDBY', remote='AcerP1165', button='Right')
    worker.addFilter(denon.send, args='TMAM', remote='AcerP1165', button='Freeze')
    worker.addFilter(denon.send, args='TMFM', remote='AcerP1165', button='Hide')
    worker.addFilter(os.system, args='dose_ein 3', button='Zoom')
    worker.addFilter(os.system, args='dose_aus 3', button='Resync')
    worker.addFilter(lgPlasma.mutevideo, args='Power2', remote='Hauppauge6400')
    worker.addFilter(lgPlasma.init, remote='Receiver12V', button='Power')
    worker.addFilter(lgPlasma.send, args='poweroff', remote='Receiver12V', button='0')
    worker.addFilter(lgPlasma.send, args='inputdtv', remote='Receiver12V', button='1')
    worker.addFilter(lgPlasma.send, args='inputhdmi1', remote='Receiver12V', button='2')
    worker.addFilter(lgPlasma.send, args='inputhdmi2', remote='Receiver12V', button='3')
    worker.addFilter(lgPlasma.send, args='inputcomponent', remote='Receiver12V', button='4')
    worker.addFilter(lgPlasma.send, args='inputanalog', remote='Receiver12V', button='5')
    worker.addFilter(vdr.switchVt, args='desktop', remote='Receiver12V', button='6')
    worker.addFilter(vdr.switchVt, args='video', remote='Receiver12V', button='7')

    morning = MorningAction(worker, vdr, denon, lgPlasma) # pylint: disable=W0612

    while True:
        event = irw.read()
        for appliance in appliances:
            appliance.setEvent(event)
        try:
            worker.execute(event)
        except Exception as exception: # pylint: disable=W0703
            LOGGER.error('%s: %s' % (event, exception), exc_info=True)

if __name__ == "__main__":
    main()
