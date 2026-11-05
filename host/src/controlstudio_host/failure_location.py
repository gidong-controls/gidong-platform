"""Bounded code location only; never exception text, locals, or absolute paths."""
from __future__ import annotations

import re
from pathlib import Path

_PHASES = {'host_before_native', 'native_execution'}
_MODULE = re.compile(r'controlstudio_host(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,7}\Z', re.ASCII)
_FUNCTION = re.compile(r'(?:[A-Za-z_][A-Za-z0-9_]{0,95}|<(?:lambda|listcomp|dictcomp|setcomp|genexpr)>)\Z', re.ASCII)


def safe_host_failure_location(error, *, native_execution_started):
    phase = 'native_execution' if native_execution_started is True else 'host_before_native'
    result = {'phase': phase, 'frame': None}
    try:
        root = Path(__file__).resolve().parent
        traceback = error.__traceback__
        for _ in range(64):
            if traceback is None:
                break
            frame, line = traceback.tb_frame, traceback.tb_lineno
            module = frame.f_globals.get('__name__')
            function = frame.f_code.co_name
            filename = frame.f_globals.get('__file__')
            if (type(module) is str and len(module) <= 160 and _MODULE.fullmatch(module)
                    and type(function) is str and _FUNCTION.fullmatch(function)
                    and type(line) is int and 1 <= line <= 10_000_000
                    and type(filename) is str):
                relative = Path(*module.split('.')[1:])
                candidates = (root / relative.with_suffix('.py'), root / relative / '__init__.py') if relative.parts else (root / '__init__.py',)
                loaded = Path(filename).resolve()
                owned = next((path for path in candidates if loaded == path.resolve()), None)
                code = frame.f_code.co_filename.replace('\\', '/')
                if owned is not None and (code == str(owned).replace('\\', '/')
                        or code.endswith('/controlstudio_host/' + owned.relative_to(root).as_posix())
                        or code == 'controlstudio_host/' + owned.relative_to(root).as_posix()):
                    result['frame'] = {'module': module, 'function': function, 'line': line}
            traceback = traceback.tb_next
    except Exception:
        # A diagnostic cannot replace the original failure.
        pass
    return result


def host_failure_location_detail(value):
    try:
        if type(value) is not dict or set(value) != {'phase', 'frame'} or value['phase'] not in _PHASES:
            return ''
        frame = value['frame']
        location = 'none'
        if frame is not None:
            if (type(frame) is not dict or set(frame) != {'module', 'function', 'line'}
                    or type(frame['module']) is not str or len(frame['module']) > 160
                    or not _MODULE.fullmatch(frame['module'])
                    or type(frame['function']) is not str or not _FUNCTION.fullmatch(frame['function'])
                    or type(frame['line']) is not int or not 1 <= frame['line'] <= 10_000_000):
                return ''
            location = f"{frame['module']}/{frame['function']}:{frame['line']}"
        return f" Safe Host location: phase={value['phase']}; frame={location}."
    except Exception:
        return ''
