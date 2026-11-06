"""Open Windows voice typing in the existing dialogue composer.

Windows supplies recognition; the application never handles a second command
language or sends an automatic message. The user reviews and submits the text.
References: Microsoft Support voice typing (Win+H), Win32 SendInput.
"""
import ctypes
import os


class _Mouse(ctypes.Structure):
    _fields_=[('dx',ctypes.c_int32),('dy',ctypes.c_int32),('mouseData',ctypes.c_uint32),
              ('dwFlags',ctypes.c_uint32),('time',ctypes.c_uint32),('dwExtraInfo',ctypes.c_size_t)]


class _Keyboard(ctypes.Structure):
    _fields_=[('wVk',ctypes.c_uint16),('wScan',ctypes.c_uint16),('dwFlags',ctypes.c_uint32),
              ('time',ctypes.c_uint32),('dwExtraInfo',ctypes.c_size_t)]


class _Hardware(ctypes.Structure):
    _fields_=[('uMsg',ctypes.c_uint32),('wParamL',ctypes.c_uint16),('wParamH',ctypes.c_uint16)]


class _Payload(ctypes.Union):
    _fields_=[('mi',_Mouse),('ki',_Keyboard),('hi',_Hardware)]


class _Input(ctypes.Structure):
    _fields_=[('type',ctypes.c_uint32),('payload',_Payload)]


def _key(code,flags=0):return _Input(1,_Payload(ki=_Keyboard(code,0,flags,0,0)))


def start_windows_dictation(*,send_input=None,platform_name=None):
    if (os.name if platform_name is None else platform_name)!='nt':
        raise RuntimeError('Кнопка диктовки использует голосовой ввод Windows. На этой системе он недоступен.')
    if send_input is None:
        send_input=ctypes.WinDLL('user32',use_last_error=True).SendInput
        send_input.argtypes=[ctypes.c_uint,ctypes.POINTER(_Input),ctypes.c_int]
        send_input.restype=ctypes.c_uint
    events=(_Input*4)(_key(0x5B),_key(0x48),_key(0x48,2),_key(0x5B,2))
    if send_input(4,events,ctypes.sizeof(_Input))!=4:
        releases=(_Input*2)(_key(0x48,2),_key(0x5B,2))
        send_input(2,releases,ctypes.sizeof(_Input))
        raise RuntimeError('Не удалось открыть голосовой ввод Windows. Нажмите Win+H в поле сообщения.')
