"""Session-owned US fast clock. Never reads or writes the EU runtime clock."""
import math
import re
import time


def clock_settings(value):
    if not isinstance(value, dict):
        raise ValueError('US clock settings must be an object')
    clock = value.get('clock_time', '12:00')
    speed = value.get('clock_speed', 1)
    if not isinstance(clock, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', clock):
        raise ValueError('US clock time must be HH:MM or HH:MM:SS')
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0 < speed <= 60:
        raise ValueError('US clock speed must be greater than 0 and at most 60')
    parts = list(map(int, clock.split(':')))
    return {'seconds': parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0),
            'speed': speed, 'running': False, 'anchor': time.time()}


def clock_status(clock):
    seconds = clock['seconds'] + (max(0, time.time() - clock['anchor']) * clock['speed'] if clock['running'] else 0)
    whole = int(seconds) % 86400
    return {'time': f'{whole // 3600:02}:{whole % 3600 // 60:02}:{whole % 60:02}',
            'speed': clock['speed'], 'running': clock['running'], 'seconds': seconds,
            'day_offset': int(seconds) // 86400, 'scope': 'us'}
