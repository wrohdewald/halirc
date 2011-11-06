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

import time, datetime
from lib import OPTIONS, LOGGER, SerialDevice, elapsedSince

class Denon(SerialDevice):
    """support the more important things the denon interface
    lets us do. Denon has published AVR-3805SerialProtocolv4-0.PDF
    which mostly also works for my AVR-2805 and maybe others.

    The Denon has a problem with commands in fast succession: after
    some commands, some others are ignored if they are sent too soon.
    Between SI..SI (Select Input) a small time is needed. But for
    MV (Master Volume) after SI, we must wait for about 2 seconds,
    or the MV command will silently be ignored. There might be
    more sophisticated ways to handle this like waiting for a
    response with SI - but that would not be simpler. If several
    SI are sent in quick sequence, Denon will send a response for
    only one of them even if it executes them all
    self.delays defines waiting times for between command A and B

    An alternative solution is to repeatedly send the command until
    the Denon sends the wanted answer. Like def standby(). But some
    Denon commands never send answers at all...

    """

    def __init__(self, device='/dev/denon'):
        SerialDevice.__init__(self, device)
        self.mutedVolume = None
        self.current = {}
        self.lastSentCmd = None
        self.lastSentTime = None
        # virtually never close because the Denon sends events
        # by its own if it is operated by other means (IR, front knobs)
        self.closeAfterUnusedSeconds = 10000000
        self.delays = {'SIMV': 2.0, 'xW..': 2.0}

    def setEvent(self, event):
        """event is the next event to be processed or None if there
        was no event while timeout"""
        SerialDevice.setEvent(self, event)
        if not event:
            self.getResponses()

    def init(self):
        """initialize the Denon to sane values"""
        if not self.isPoweredOn():
            self.send('PWON')
            while self.current['PW'] != 'ON':
                self.getResponses()
                self.send('PWON')
                time.sleep(0.5)

    def standby(self):
        """set the device to standby"""
        if self.isPoweredOn():
            while self.current['PW'] == 'ON':
                self.getResponses()
                self.send('PWSTANDBY')
                time.sleep(0.5)
        self.mutedVolume = None

    def delay(self, dummyCommand=None, dummyParameters=None):
        return 0.02

    # pylint: disable=R0201
    def parse(self, data):
        """split data into key, index and value.
        index is None if a request has only one return value"""
        key, subcmd, index, maxIndex, value = data[:2], None, None, None, data[2:]
        if value != '?':
            if key == 'CV':
                subcmd, value = value.split(' ')
                subcommands = ['FL', 'FR', 'C', 'SW',
                     'SL', 'SR', 'SBL', 'SBR', 'SB']
                index = subcommands.index(subcmd)
                maxIndex = len(subcommands)
            elif key in ['Z1', 'Z2']:
                if value in ['ON', 'OFF']:
                    index = 2
                elif value[0] in '0123456789':
                    index = 1
                else:
                    index = 0
                maxIndex = 3
                subcmd = str(index)
            elif key == 'TM':
                if value in ['AM', 'FM']:
                    index = 0
                else:
                    index = 1
                maxIndex = 2
                subcmd = str(index)
        return key, subcmd, index, maxIndex, value

    def updateStatus(self, data):
        """update self.current with data received from Denon"""
        assert data[2] != '?'
        key, subcmd, index, maxIndex, value = self.parse(data)
        oldValue = self.current.get(key, None)
        if oldValue is not None and index is not None:
            oldValue = oldValue[index]
        if value != oldValue:
            if 'c' in OPTIONS.debug:
                LOGGER.debug('Denon: %s%s: %s --> %s' % (key, subcmd or '', oldValue, value))
        if index is None:
            self.current[key] = value
        else:
            if key not in self.current:
                self.current[key] = [None] * maxIndex
            self.current[key][index] = value

    def getResponses(self, expect=None):
        """get all pending responses or events.
        If expect (the expected response/event) is given,
        return its last value received within this call or None
        Since some commands take more time until they send
        events, the main loop of halirc also calls us for polling
        """
        result = None
        while True:
            data = self.readline('\r')
            if not data:
                break
            self.updateStatus(data)
            key, _, _, _, value = self.parse(data)
            if expect and key == self.parse(expect)[0]:
                result = value
                break # more responses will be read later
        return result

    def maybeDelay(self, cmd):
        """do we need to wait before sending this command?"""
        if not self.lastSentCmd:
            return
        cmd1 = self.lastSentCmd[:2]
        cmd2 = cmd[:2]
        delay = 0
        for key in (cmd1 + cmd2, cmd1 + '..', '..' + cmd2):
            if key in self.delays and self.delays[key] > delay:
                delay = self.delays[key]
        if delay:
            stillWaiting = delay - elapsedSince(self.lastSentTime)
            if stillWaiting > 0:
                if 's' in OPTIONS.debug:
                    LOGGER.debug('sleeping %s/%s between %s and %s' % ( \
                        stillWaiting, delay, self.lastSentCmd, cmd))
                time.sleep(stillWaiting)

    def send(self, cmd):
        """send cmd to Denon and return the answers if there are any
        some commands will return an answer as different command or no answer
        """
        key, _, _, _, _ = self.parse(cmd)
        self.maybeDelay(cmd)
        if key != 'PW' and not self.isPoweredOn():
            return ''
        self.communicate(cmd + '\r')
        if cmd[2] != '?':
            self.lastSentCmd = cmd
            self.lastSentTime = datetime.datetime.now()
            if key == 'TM':
                # the receiver does not confirm this
                self.updateStatus(cmd)
                return cmd
            elif key == 'SV':
                # the receiver does not confirm this
                if cmd != 'SVSOURCE':
                    self.updateStatus(cmd)
                return cmd
        return self.getResponses(cmd)

    def getAnswer(self, cmd):
        """ask Denon unless we already know the current value"""
        command = self.parse(cmd)[0]
        while not command in self.current:
            self.send(command + '?')
        return self.current[command]

    def sendIfNot(self, cmd):
        """if Denon is not on the wanted value, set it to it"""
        _, _, _, _, value = self.parse(cmd)
        if self.getAnswer(cmd) != value:
            self.send(cmd)

    def isPoweredOn(self):
        """is Denon powered on?"""
        return self.getAnswer('PW') == 'ON'

    def volume(self, newValue):
        """change volume up or down"""
        if self.isPoweredOn():
            if self.mutedVolume:
                self.mute()
            else:
                self.send('MV%s' % newValue)

    def mute(self):
        """toggle between mute/unmuted"""
        if self.isPoweredOn():
            if self.mutedVolume:
                newMV = self.mutedVolume
                self.mutedVolume = None
            else:
                while not 'MV' in self.current:
                    self.send('MV?')
                self.mutedVolume = self.current['MV']
                newMV = '20'
            self.send('MV%s' % newMV)

    def queryStatus(self, full=False):
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
            commands = ['PS', 'ZM', 'PW', 'TP', 'MU', 'SI',
                'MV', 'MS', 'TF', 'CV', 'Z1', 'Z2', 'TM', 'SR']
        for command in commands:
            self.send('%s?' % command)
        for _ in range(0, 10):
            self.getResponses()
        if 'r' in OPTIONS.debug:
            LOGGER.debug('current status:%s ' % self.current)

