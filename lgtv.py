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

import sys, datetime

from twisted.protocols.basic import LineOnlyReceiver
from twisted.internet.defer import succeed
from twisted.internet import reactor

from lib import Message, MessageEvent, Serializer, \
    LOGGER, elapsedSince

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
        values['volume']['%02X' % volume] = str(volume)

    def __init__(self, decoded=None, encoded=None):
        self.setID = '01'
        Message.__init__(self, decoded, encoded)

    def _setAttributes(self, decoded, encoded):
        """The LG interface is a but
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
                LOGGER.error('answer from LG matches more than 1 command: %s' % items)
            elif len(items) == 0:
                LOGGER.error('answer from LG matches no command: %s' % self.encoded)
            humanCommand = items[0][0]
            if encodedValue:
                decodedValue = self.values[humanCommand][encodedValue]
            else:
                decodedValue = 'None'
            self._decoded = ':'.join([humanCommand, decodedValue])
        else: # decoded
            self._decoded = decoded
            humanCommand = self.humanCommand()
            humanValue = self.humanValue()
            if humanValue:
                encodedValue = [x[0] for x in self.values[humanCommand].items() if x[1] == humanValue][0]
            else:
                self.ask = True
                encodedValue = 'ff'
        self._encoded = ' '.join([self.commands[humanCommand], self.setID, encodedValue])

    def humanCommand(self):
        result = self._decoded.split(':')[0]
        if result:
            if result not in self.commands:
                LOGGER.critical('LGTV: unknown argument %s' % self._decoded)
                sys.exit(2)
        return result

    def humanValue(self):
        return ':'.join(self._decoded.split(':')[1:])

    def command(self):
        """the command of this message, encoded"""
        return self._encoded.split()[0]

    def value(self):
        """the value of this message, encoded"""
        return self._encoded.split()[2]

class LGTV(LineOnlyReceiver, Serializer):
    """Interface to probably most LG flatscreens"""
    delimiter = 'x' # for LineOnlyReceiver
    message = LGTVMessage

    def __init__(self, hal):
        Serializer.__init__(self)
        self.hal = hal
        self.videoMuted = None
        self.tvTimeout = 300

    def delay(self, previous, dummyThis):
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

    def send(self, *args):
        """if wanted value for cmd is not set in LGTV, set it.
        If we are acting on an event from the LG remote:
        the LG TV is quite fast in executing those events, so
        when we get here, the LG TV should already return the
        wanted value if it received the LG remote event too
        """
        _, msg = self.args2message(args)
        def got(result):
            """now we know the current value"""
            if result.value() != msg.value():
                return self.push(msg)
            else:
                return succeed(None)
        return self.getAnswer(msg.humanCommand()).addCallback(got)

    def getAnswer(self, cmd):
        """ask the LGTV for a value"""
        return self.push(self.message(cmd))

    def standby(self, *dummyArgs):
        """power off the LGTV"""
        self.send('power:off')

    def standbyIfUnused(self):
        """if we are muted long enough, go to standby"""
        if self.videoMuted and elapsedSince(self.videoMuted) + 1 > self.tvTimeout:
            self.standby(None)

    def mutescreen(self, event, muteButton):
        """except for muteButton, all remote buttons make video visible again"""
        if event.button == muteButton:
            self.videoMuted = datetime.datetime.now()
            # pylint: disable=E1101
            reactor.callLater(self.tvTimeout, self.standbyIfUnused)
            return self.send('mutescreen:on')
        else:
            def got1(answer):
                """got answer"""
                if answer.humanValue() != 'on':
                    return self.init()
                else:
                    return self.getAnswer('mutescreen').addCallback(got2)
            def got2(answer):
                """got answer"""
                if answer.humanValue() != 'off':
                    return self.init()
                else:
                    return succeed(None)
            return self.getAnswer('power').addCallback(got1)

    def lineReceived(self, data):
        """we got a line from LGTV"""
        msg = self.message(encoded=data)
        if self.tasks.running:
            self.tasks.gotAnswer(msg)
        else:
            # this has been triggered by other means like the
            # original remote control or the front elements of Denon
            self.hal.eventReceived(MessageEvent(msg))

