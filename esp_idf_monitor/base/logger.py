# SPDX-FileCopyrightText: 2015-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import datetime
import os
import re
from typing import AnyStr, BinaryIO, Callable, Optional  # noqa: F401

from serial.tools import miniterm  # noqa: F401

from .constants import ADDRESS_RE
from .output_helpers import lookup_pc_address, red_print, yellow_print
from .pc_address_matcher import PcAddressMatcher


class Logger:
    def __init__(self, elf_file, console, timestamps, timestamp_format, pc_address_buffer, enable_address_decoding,
                 toolchain_prefix, rom_elf_file=None, log_filename=None):
        # type: (str, miniterm.Console, bool, str, bytes, bool, str, Optional[str]) -> None
        self.log_file = None  # type: Optional[BinaryIO]
        self.log_filename = log_filename
        self._output_enabled = True  # type: bool
        self._start_of_line = True  # type: bool
        self.elf_file = elf_file
        self.rom_elf_file = rom_elf_file
        self.console = console
        self.timestamps = timestamps
        self.timestamp_format = timestamp_format
        self._pc_address_buffer = pc_address_buffer
        self.enable_address_decoding = enable_address_decoding
        self.toolchain_prefix = toolchain_prefix
        if enable_address_decoding:
            self.pc_address_matcher = PcAddressMatcher(self.elf_file)
            if rom_elf_file is not None:
                self.rom_pc_address_matcher = PcAddressMatcher(self.rom_elf_file)  # type: ignore

        if self.log_filename:
            self.start_logging()

    @property
    def pc_address_buffer(self):  # type: () -> bytes
        return self._pc_address_buffer

    @pc_address_buffer.setter
    def pc_address_buffer(self, value):  # type: (bytes) -> None
        self._pc_address_buffer = value

    @property
    def output_enabled(self):  # type: () -> bool
        return self._output_enabled

    @output_enabled.setter
    def output_enabled(self, value):  # type: (bool) -> None
        self._output_enabled = value

    @property
    def log_file(self):  # type: () -> Optional[BinaryIO]
        return self._log_file

    @log_file.setter
    def log_file(self, value):  # type: (Optional[BinaryIO]) -> None
        self._log_file = value

    def toggle_logging(self):  # type: () -> None
        if self._log_file:
            self.stop_logging()
        else:
            self.start_logging()

    def toggle_timestamps(self):  # type: () -> None
        self.timestamps = not self.timestamps

    def start_logging(self):  # type: () -> None
        if not self._log_file:
            if self.log_filename:
                name = '{}.{}.log'.format(self.log_filename, datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
            else:
                name = 'log.{}.{}.txt'.format(os.path.splitext(os.path.basename(self.elf_file))[0],
                                              datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
            try:
                self.log_file = open(name, 'wb+')
                yellow_print('\nLogging is enabled into file {}'.format(name))
            except Exception as e:  # noqa
                red_print('\nLog file {} cannot be created: {}'.format(name, e))

    def stop_logging(self):  # type: () -> None
        if self._log_file:
            try:
                name = self._log_file.name
                self._log_file.close()
                yellow_print('\nLogging is disabled and file {} has been closed'.format(name))
            except Exception as e:  # noqa
                red_print('\nLog file cannot be closed: {}'.format(e))
            finally:
                self._log_file = None

    def print(self, string, console_printer=None):
        # type: (AnyStr, Optional[Callable]) -> None
        if console_printer is None:
            console_printer = self.console.write_bytes

        if isinstance(string, type(u'')):
            new_line_char = '\n'
        else:
            new_line_char = b'\n'  # type: ignore

        if string and self.timestamps and (self._output_enabled or self._log_file):
            t = datetime.datetime.now().strftime(self.timestamp_format)

            # "string" is not guaranteed to be a full line. Timestamps should be only at the beginning of lines.
            if isinstance(string, type(u'')):
                line_prefix = t + ' '
            else:
                line_prefix = t.encode('ascii') + b' '  # type: ignore

            # If the output is at the start of a new line, prefix it with the timestamp text.
            if self._start_of_line:
                string = line_prefix + string

            # If the new output ends with a newline, remove it so that we don't add a trailing timestamp.
            self._start_of_line = string.endswith(new_line_char)
            if self._start_of_line:
                string = string[:-len(new_line_char)]

            string = string.replace(new_line_char, new_line_char + line_prefix)

            # If we're at the start of a new line again, restore the final newline.
            if self._start_of_line:
                string += new_line_char
        elif string:
            self._start_of_line = string.endswith(new_line_char)

        if self._output_enabled:
            console_printer(string)
        if self._log_file:
            try:
                if isinstance(string, type(u'')):
                    string = string.encode()  # type: ignore
                self._log_file.write(string)  # type: ignore
            except Exception as e:
                red_print('\nCannot write to file: {}'.format(e))
                # don't fill-up the screen with the previous errors (probably consequent prints would fail also)
                self.stop_logging()

    def output_toggle(self):  # type: () -> None
        self.output_enabled = not self.output_enabled
        yellow_print('\nToggle output display: {}, Type Ctrl-T Ctrl-Y to show/disable output again.'.format(
            self.output_enabled))

    def handle_possible_pc_address_in_line(self, line):  # type: (bytes) -> None
        line = self._pc_address_buffer + line
        self._pc_address_buffer = b''
        if not self.enable_address_decoding:
            return
        for m in re.finditer(ADDRESS_RE, line.decode(errors='ignore')):
            num = m.group()
            address_int = int(num, 16)
            translation = None

            # Try looking for the address in the app ELF file
            if self.pc_address_matcher.is_executable_address(address_int):
                translation = lookup_pc_address(num, self.toolchain_prefix, self.elf_file)
            # Not found in app ELF file, check ROM ELF file (if it is available)
            if translation is None and self.rom_elf_file is not None and self.rom_pc_address_matcher.is_executable_address(address_int):
                translation = lookup_pc_address(num, self.toolchain_prefix, self.rom_elf_file, is_rom=True)

            # Translation found either in the app or ROM ELF file
            if translation is not None:
                self.print(translation, console_printer=yellow_print)
