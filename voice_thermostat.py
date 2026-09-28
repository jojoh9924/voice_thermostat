#!/usr/bin/env python3
"""Push-to-talk SmartRent thermostat control for a Jetson and USB microphone.

Install in the existing environment: pip install vosk==0.3.45 smartrent-py==0.5.2
Requires arecord (Ubuntu package alsa-utils) and vosk-model-small-en-us-0.15.
Speech stays local; SmartRent uses its cloud through an unofficial client.
Credentials are prompted for each run and never saved by this script.
"""
import argparse
import asyncio
from array import array
import getpass
import json
import logging
from pathlib import Path
import re
import subprocess
import sys


def number_words(n):
    small = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
             'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
             'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
    if n < 20:
        return small[n]
    tens = {2: 'twenty', 3: 'thirty', 4: 'forty', 5: 'fifty',
            6: 'sixty', 7: 'seventy', 8: 'eighty', 9: 'ninety'}
    return tens[n // 10] + (' ' + small[n % 10] if n % 10 else '')


NUMBERS = {number_words(n): n for n in range(100)}


def parse_command(text, units):
    """Only accept complete, explicitly supported commands; never guess."""
    text = ' '.join(text.lower().replace('-', ' ').split())
    if text in ('what is the temperature', 'check temperature'):
        return 'status', None
    if text in ('cancel', 'stop', 'quit'):
        return 'cancel', None
    match = re.fullmatch(
        r'set (?:the )?(?:temperature|thermostat) to ([a-z0-9 ]+?)'
        r'(?: degrees)?(?: (fahrenheit|celsius))?', text)
    if not match:
        raise ValueError('Say: set temperature to seventy two, or check temperature.')
    number, spoken_units = match.groups()
    if spoken_units and spoken_units[0].upper() != units:
        raise ValueError('Spoken units differ from the units selected at startup.')
    target = int(number) if number.isdigit() else NUMBERS.get(number)
    lower, upper = (60, 85) if units == 'F' else (16, 29)
    if target is None or not lower <= target <= upper:
        raise ValueError(f'Use a whole-number target between {lower} and {upper} {units}.')
    return 'set', target


def listen(model, device):
    from vosk import KaldiRecognizer
    print('Listening for 6 seconds. Speak now...', flush=True)
    result = subprocess.run(
        ['arecord', '-q', '-D', device, '-t', 'raw', '-f', 'S16_LE',
         '-r', '16000', '-c', '1', '-d', '6'],
        capture_output=True, timeout=12)
    if result.returncode:
        detail = result.stderr.decode(errors='replace').strip()
        raise RuntimeError('Microphone recording failed: ' + detail)
    samples = array('h', result.stdout)
    if sys.byteorder != 'little':
        samples.byteswap()
    if len(samples) < 16000:
        raise RuntimeError('Recording was too short. Check the microphone connection.')
    rms = (sum(int(s) ** 2 for s in samples) / len(samples)) ** 0.5
    if rms < 10:
        print('Audio is nearly silent. Check the microphone mute button and input level.')
    recognizer = KaldiRecognizer(model, 16000)
    pieces = []
    for offset in range(0, len(result.stdout), 8000):
        if recognizer.AcceptWaveform(result.stdout[offset:offset + 8000]):
            pieces.append(json.loads(recognizer.Result()).get('text', ''))
    pieces.append(json.loads(recognizer.FinalResult()).get('text', ''))
    text = ' '.join(part for part in pieces if part).strip()
    print('Heard:', text or '(no speech recognized)')
    return text


async def control(model, args):
    import aiohttp
    from smartrent import async_login
    units = 'F'
    email = input('SmartRent email: ').strip()
    password = getpass.getpass('SmartRent password: ')
    async with aiohttp.ClientSession() as session:
        print('Connecting to SmartRent...')
        api = await asyncio.wait_for(async_login(
            email, password, aiohttp_session=session, tfa_token= None), 45)
        thermostats = api.get_thermostats()
        if len(thermostats) != 1:
            print('Expected one thermostat; found', len(thermostats))
            return
        thermostat = thermostats[0]
        print('Connected. Say: set temperature to seventy two, or check temperature.')
        while True:
            if input('\nPress Enter to speak, or type q to quit: ').strip().lower() == 'q':
                return
            try:
                text = listen(model, args.device)
                if not text:
                    continue
                action, target = parse_command(text, units)
                if action == 'cancel':
                    print('Cancelled. No change sent.')
                    continue
                # This refresh method is verified against the pinned client 0.5.2.
                await asyncio.wait_for(thermostat._async_fetch_state(), 20)
                mode = thermostat.get_mode()
                print('Room:', thermostat.get_current_temp(),
                      '| Mode:', mode,
                      '| Cooling target:', thermostat.get_cooling_setpoint(),
                      '| Heating target:', thermostat.get_heating_setpoint())
                if action == 'status':
                    continue
                if mode not in ('heat', 'cool'):
                    print('Select Heat or Cool in SmartRent first. No change sent.')
                    continue
                current = (thermostat.get_cooling_setpoint() if mode == 'cool'
                           else thermostat.get_heating_setpoint())
                if current is None or (units == 'F' and current < 45) or (units == 'C' and current > 40):
                    print('Current target is missing or units appear inconsistent. No change sent.')
                    continue
                # Recheck mode after confirmation in case someone changed it in the app.
                await asyncio.wait_for(thermostat._async_fetch_state(), 20)
                if thermostat.get_mode() != mode:
                    print('Mode changed while waiting. No change sent; try again.')
                    continue
                setter = (thermostat.async_set_cooling_setpoint if mode == 'cool'
                          else thermostat.async_set_heating_setpoint)
                await asyncio.wait_for(setter(target), 30)
                # The library updates its cache optimistically: this is not device confirmation.
                print('Command sent. Verify the target in SmartRent; room temperature changes slowly.')
            except ValueError as exc:
                print(exc)
            except (subprocess.TimeoutExpired, RuntimeError) as exc:
                print('Microphone error:', exc)
            except asyncio.TimeoutError:
                print('SmartRent timed out. Check the app before retrying; a command may have arrived.')
            except Exception as exc:
                # Avoid printing HTTP responses, tokens, or account identifiers.
                print('SmartRent error:', type(exc).__name__, '- restart the script to sign in again.')
                return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-mic', action='store_true', help='Transcribe only; no SmartRent login')
    parser.add_argument('--device', default='plughw:CARD=MICROPHONE,DEV=0')
    parser.add_argument('--model', type=Path,
                        default=Path.home() / 'smart-temp' / 'vosk-model-small-en-us-0.15')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if not args.model.is_dir():
        print('Speech model missing:', args.model)
        return 1
    try:
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        print('Loading speech model...')
        model = Model(str(args.model))
        if args.test_mic:
            input('Press Enter, then say: set temperature to seventy two. ')
            listen(model, args.device)
        else:
            asyncio.run(control(model, args))
    except (KeyboardInterrupt, EOFError):
        print('\nStopped.')
    except FileNotFoundError:
        print('arecord is missing. Install it with: sudo apt install alsa-utils')
        return 1
    except Exception as exc:
        print('Setup or login failed:', type(exc).__name__)
        print('For recording problems run: arecord -l. Share the error name for other failures.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
