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

import sys, os, datetime

from twisted.protocols.basic import LineOnlyReceiver
from twisted.internet.defer import succeed
from twisted.internet import reactor
from twisted.internet.serialport import SerialPort

from lib import Message, Serializer, LOGGER, elapsedSince

class LGTVMessage(Message):
    """holds content of a message from or to a LG TV"""
    # commands holds an entry for every command/value combination"""

    commands = {
            'power': 'ka',
            'mutesound': 'ke',
            'osd': 'kl',
            'aspect': 'kc',
            'input': 'xb',
            'mutescreen': 'kd',
            'volume': 'kf'
    }
    values = {
        'power': {'00': 'off', '01': 'on'},
        'mutesound': {'00': 'on', '01': 'off'},
        'osd': {'00': 'off', '01': 'on'},
        'aspect': {'01': '4:3', '02': '16:9', '04': 'zoom', '06': 'original',
              '07': '14:9', '09': 'scan', '0B': 'full width'},
        'input': {'00': 'DTV', '10': 'analog', '20': 'AV', '40': 'Component',
              '90': 'HDMI1', '91': 'HDMI2'},
        'mutescreen': {'00': 'off', '01': 'on', '10': 'only sound'},
        'volume': {}
    }

    for zoom in range(1, 17):
        values['aspect']['%02X' % (zoom + 15)] = 'cinema%d' % zoom

    for volume in range(0, 64):
        values['volume']['%02x' % volume] = str(volume)

    def __init__(self, decoded=None, encoded=None):
        self.setID = '01'
        Message.__init__(self, decoded, encoded)

    def _setAttributes(self, decoded, encoded):
        """The LG interface is a bit
        stupid: When sending, we send two characters for the command
        like ka or ma. But the answer only returns the second character,
        so we cannot easily find the corresponding full command"""
        if encoded is not None:
            parts = encoded.split(' ')
            if len(parts) < 3:
                self._decoded = ':'
                return
            self.status = parts[2][:2]
            cmd2 = parts[0]
            self.status = parts[2][:2]
            encodedValue = parts[2][2:]
            items = [x for x in self.commands.items() if x[1][1] == cmd2]
            if len(items) > 1:
                LOGGER.error('answer from LG matches more than 1 command: {}'.format(items))
            elif len(items) == 0:
                LOGGER.error('answer from LG matches no command: {}'.format(self.encoded))
            humanCommand = items[0][0]
            if encodedValue:
                decodedValue = self.values[humanCommand][encodedValue]
            else:
                decodedValue = 'None'
            self._decoded = ':'.join([humanCommand, decodedValue])
        else: # decoded
            self._decoded = decoded
            humanCommand = self.humanCommand()
            humanValue = self.value()
            if humanValue:
                encodedValue = [x[0] for x in self.values[humanCommand].items() if x[1] == humanValue][0]
            else:
                self.isQuestion = True
                encodedValue = 'ff'
        self._encoded = ' '.join([self.commands[humanCommand], self.setID, encodedValue])

    def humanCommand(self):
        result = self._decoded.split(':')[0]
        if result:
            if result not in self.commands:
                LOGGER.critical('LGTV: unknown argument {}'.format(self._decoded))
                sys.exit(2)
        return result

    def value(self):
        """the human readable value of this message"""
        return ':'.join(self._decoded.split(':')[1:])

    def command(self):
        """the command of this message, encoded"""
        return self._encoded.split()[0]

class LGTV(LineOnlyReceiver, Serializer):
    """Interface to probably most LG flatscreens"""
    delimiter = 'x' # for LineOnlyReceiver
    message = LGTVMessage
    poweronCommands = ('input')

    def __init__(self, hal, device='/dev/LGPlasma', outlet=None):
        Serializer.__init__(self, hal, outlet)
        self.device = device
        self.videoMuted = None
        self.tvTimeout = 300
        self.connect()

    def connect(self):
        """connect to serial port"""
        if not os.path.exists(self.device):
            self.connected = False
            LOGGER.info('LGTV: {} does not exist, waiting for 0.1 seconds'.format(self.device))
            reactor.callLater(0.1, self.connect)
        else:
            SerialPort(self, self.device, reactor)
            self.connected = True
            LOGGER.info('LGTV: connected to {}'.format(self.device))

    def connectionLost(self, reason):
        """USB disconnect or similar"""
        self.connected = False
        LOGGER.error('LGTV lost connection: {}'.format(reason))
        reactor.callLater(5, self.connect)

    @staticmethod
    def delay(previous, dummyThis):
        """compute delay between two requests. If we send commands
        while the LG is powering on or off, it might ignore them
        or return garbage or become unresponsive to further commands"""
        cmd1 = previous.message.decoded if previous else ''
        result = 0
        if cmd1 == 'power:on':
            # poweron is faster!
            result = 6
        elif cmd1 == 'power:off':
            # poweroff needs EIGHT seconds!
            result = 8
        return result

    def init(self):
        """init the LGTV for VDR usage"""
        self.videoMuted = None
        return self.send(None, 'power:on').addCallback(
           self.send, 'volume:0').addCallback(
           self.send, 'aspect:scan').addCallback(
           self.send, 'mutescreen:off')

    def _poweron(self, *dummyArgs):
        """power on the LGTV"""
        return self.send('power:on').addCallback(self.send, 'mutescreen:off')

    def _standby(self, *dummyArgs):
        """power off the LGTV"""
        return self.send('power:off')

    def standbyIfUnused(self):
        """if we are muted long enough, go to standby"""
        if self.videoMuted and elapsedSince(self.videoMuted) + 1 > self.tvTimeout:
            return self.standby(None)

    def mutescreen(self, event, muteButton, receiver):
        """except for muteButton, all remote buttons make video visible again"""
        def got1(answer):
            """got answer"""
            if answer.value() != 'on':
                return self.init()
            else:
                return self.ask('mutescreen').addCallback(got2)
        def got2(answer):
            """got answer"""
            if answer.value() == 'on' or event.button != muteButton:
                newValue = 'off'
            else:
                newValue = 'on'
            if answer.value() == newValue:
                return succeed(None)
            if newValue == 'on':
                self.videoMuted = datetime.datetime.now()
                reactor.callLater(self.tvTimeout, self.standbyIfUnused)
                return self.reallySend('mutescreen:on')
            else:
                receiver.poweron()
                return self.init()
        return self.ask('power').addCallback(got1)

    def aspect(self, dummyEvent, cycle):
        """cycle aspect ratio between our preferred values"""
        def got1(answer):
            """got answer"""
            if answer.value() not in cycle:
                newValue = cycle[0]
            else:
                newValue = (cycle + cycle)[cycle.index(answer.value()) + 1]
            return self.push('aspect:%s' % newValue)
        return self.ask('aspect').addCallback(got1)

    def lineReceived(self, data):
        Serializer.defaultInputHandler(self, data)

