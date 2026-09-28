# Voice thermostat

Push-to-talk control for a SmartRent thermostat. Speech is recognized locally with Vosk. Temperature changes go to SmartRent through the unofficial [`smartrent-py`](https://github.com/ZacheryThomas/smartrent-py) client.

The script asks for an email and password each time it runs and does not save them.

## Requirements

- Python 3
- `arecord` from the Ubuntu package `alsa-utils`
- A USB microphone
- The Vosk model `vosk-model-small-en-us-0.15`
- One SmartRent thermostat on the account, already set to Heat or Cool

```bash
sudo apt install alsa-utils
pip install vosk==0.3.45 smartrent-py==0.5.2
```

Download the model from the [Vosk models page](https://alphacephei.com/vosk/models) and unpack it to:

```text
~/smart-temp/vosk-model-small-en-us-0.15
```

Pass `--model` if you keep it somewhere else.

## Use

Check the microphone without signing in:

```bash
python3 voice_thermostat.py --test-mic
```

Sign in and control the thermostat:

```bash
python3 voice_thermostat.py
```

Press Enter, then speak during the 6-second recording window. Type `q` and press Enter to quit.

The default capture device is `plughw:CARD=MICROPHONE,DEV=0`. List devices with `arecord -l` and override the choice with `--device`.

## Commands

The script accepts only these phrases. It does not guess.

| Say | Result |
| --- | --- |
| `set temperature to seventy two` | Set the active heat or cool target |
| `set the thermostat to 72 degrees` | Same, with a spoken or numeric temperature |
| `check temperature` or `what is the temperature` | Print room temperature, mode, and both setpoints |
| `cancel`, `stop`, or `quit` | Discard that utterance and send nothing |

Targets must be whole numbers from 60 to 85 °F. The script always uses Fahrenheit. If you say Celsius, it refuses the command.

A change is sent only when the thermostat is in Heat or Cool. The script sets the cooling target in Cool and the heating target in Heat. Confirm the new target in the SmartRent app. The room temperature changes slowly, and the client updates its own cache before the device confirms the change.
