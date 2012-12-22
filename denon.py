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

import datetime

from lib import Message, Serializer, elapsedSince
from twisted.protocols.basic import LineOnlyReceiver
from twisted.internet import reactor
from twisted.internet.defer import succeed
from twisted.internet.serialport import SerialPort


class DenonMessage(Message):
    """holds content of a message from or to a Denon.
    Since the Denon protocol is rather human readable, use that
    as the human readable form - so please refer to the
    Denon RS232 API docs"""
    def __init__(self, decoded=None, encoded=None):
        """for the Denon we only use the machine form, its
        readability is acceptable"""
        Message.__init__(self, decoded, encoded)

    def _setAttributes(self, decoded, encoded):
        self._decoded = self._encoded = decoded or encoded
        if self.encoded and len(self.encoded) == 2:
            self.isQuestion = True
            self._encoded += '?'
            self._decoded += '?'

    def command(self):
        """the human readable command"""
        # TODO: subcommands
        return self._encoded[:2] if self._encoded else ''

    def value(self):
        """the human readable value"""
        if self.isQuestion:
            return ''
        else:
            return self._encoded[2:] if self._encoded else ''

    def answerMatches(self, answer):
        """does the answer match this message?"""
        if self.encoded.startswith('PSMODE:CINEMA'):
            return answer.encoded.startswith('MS') and answer.encoded.endswith('C')
        elif self.encoded.startswith('PSMODE:MUSIC'):
            return answer.encoded.startswith('MS') and answer.encoded.endswith('M')
        else:
            return Message.answerMatches(self, answer)

class Denon(LineOnlyReceiver, Serializer):
    """talk to a Denon AVR 2805 or similar"""
    delimiter = '\r'
    message = DenonMessage
    poweronCommands = ('SI')
    delays = {'PW..': 1.5, '..PW': 0.02}

    def __init__(self, hal, device='/dev/denon', outlet=None):
        """default device is /dev/denon"""
        self.mutedVolume = None
        # never close because the Denon sends events
        # by its own if it is operated by other means (IR, front knobs)
        self.surroundIdx = 0
        self.lastSurroundTime = None
        Serializer.__init__(self, hal, outlet)
        self.__port = SerialPort(self, device, reactor)

    @staticmethod
    def delay(previous, this):
        """do we need to wait before sending this command?"""
        cmd1 = previous.message.humanCommand() if previous else ''
        cmd2 = this.message.humanCommand()
        question1 = previous.message.isQuestion
        question2 = this.message.isQuestion
        result = 0
        if cmd1:
            if cmd1 == cmd2 and not question1 and question2:
                # the Denon might need a moment for its response
                result = 0.05
            elif not question1:
                for key in (cmd1 + cmd2, cmd1 + '..', '..' + cmd2):
                    if key in Denon.delays:
                        result = max(result, Denon.delays[key])
        return result

    def lineReceived(self, data):
        Serializer.defaultInputHandler(self, data)

    def standby(self, *dummyArgs):
        """switch off"""
        self.mutedVolume = None
        return self.send('PWSTANDBY')

    def poweron(self, *dummyArgs):
        """switch on"""
        return self.send('PWON')

    def send(self, *args):
        """when applicable ask Denon for current value before sending
        new value"""
        _, msg = self.args2message(*args)
        if msg.encoded in ['MVDOWN', 'MVUP']:
            # the current state is never UP or DOWN, so we just
            # send it without first asking for the old value.
            return self.push(msg)
        else:
            return Serializer.send(self, *args)

    def queryStatus(self, dummyResult, full=False):
        """query Denon status. If full, try to query even those
        parameters we do not know about"""
        if full:
            letters1 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            letters2 = letters1 + '1234567890'
            commands = []
            for letter1 in letters1:
                for letter2 in letters2:
                    commands.append(letter1 + letter2)
        else:
            # only query commands that might actually exist.
            # if full query finds more, please add them here
            commands = ['PW', 'TP', 'MU', 'SI',
                'MV', 'MS', 'TF', 'CV', 'Z2', 'TM', 'ZM']
        deferred = succeed(None)
        for command in commands:
            deferred.addCallback(self.ask, command)
        return deferred

    def volume(self, dummyResult, newValue):
        """change volume up or down or to a discrete value"""
        def _volume1(result, newValue):
            """result is ON or STANDBY"""
            if result.value() != 'ON':
                return succeed(None)
            if self.mutedVolume:
                return self.mute()
            else:
                return self.send('MV%s' % newValue)
        return self.ask('PW').addCallback(_volume1, newValue)

    def mute(self, dummyResult=None):
        """toggle between mute/unmuted"""
        def _mute1(result):
            """result is ON or STANDBY"""
            if result.value() != 'ON':
                return succeed(None)
            if self.mutedVolume:
                newMV = self.mutedVolume
                self.mutedVolume = None
                return self.push('MV%s' % newMV)
            return self.ask('MV').addCallback(_mute2)
        def _mute2(result):
            """result is the volume before unmuting"""
            self.mutedVolume = result.value()
            if self.mutedVolume < '25':
                # denon was muted when halirc started
                self.mutedVolume = None
                newMV = '40'
            else:
                newMV = '20'
            return self.push('MV%s' % newMV)
        return self.ask('PW').addCallback(_mute1)

    def surround(self, dummyEvent, osdCatEnabled, cycle):
        """cycle surround things between our preferred values"""
        commands = cycle[self.surroundIdx]
        onlyShowStatus = osdCatEnabled and self.lastSurroundTime is None or elapsedSince(self.lastSurroundTime) > 10
        self.lastSurroundTime = datetime.datetime.now()
        if onlyShowStatus:
            return self.ask('MS')
        self.surroundIdx += 1
        if self.surroundIdx == len(cycle):
            self.surroundIdx = 0
        if not isinstance(commands, list):
            commands = list([commands])
        def gotStatus(answerMsg):
            """got current status"""
            result = succeed(None)
            def gotChange(answerMsg):
                """check again after each command"""
                answer = answerMsg.decoded
                for command in commands:
                    if command == answer:
                        continue
                    if command == 'MSDTS NEO:6' and answer.startswith('MSDOLBY D'):
                        continue
                    if command == 'MSDOLBY PL2' and answer.startswith('MSDOLBY'):
                        continue
                    if command == 'PSMODE:CINEMA' and answer.endswith('C'):
                        continue
                    if command == 'PSMODE:MUSIC' and answer.endswith('M'):
                        continue
                    result.addCallback(self.push, command).addCallback(gotChange)
                    return
            return gotChange(answerMsg)
        return self.ask('MS').addCallback(gotStatus)
