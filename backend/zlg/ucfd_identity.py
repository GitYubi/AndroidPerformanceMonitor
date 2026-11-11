"""UCFD wire transport for the 3068:0009 family.

Wire format reference: wentywenty/zlg-deb@6491f7aeed8931ba0ed848ba22d4cb60a8f0112c,
usbcanfd.h and usbcanfd.c (protocol version and device info requests).
"""

from array import array
from struct import Struct

COMMAND = Struct("<BBBBI")
RESPONSE = Struct("<BBBBB3xI")
IDENTITY = Struct("<HHH40s20xBB")
INFO_OPCODE = 0x41
TIMEOUT_MS = 500


def crc16(data: bytes) -> bytes:
    crc = 0xffff
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc.to_bytes(2, "little")


def query_version(backend, handle):
    buffer = array("B", bytes(4))
    size = backend.ctrl_transfer(handle, 0xa1, 0, 0, 0, buffer, TIMEOUT_MS)
    if size not in (2, 4):
        raise ValueError(f"Unexpected protocol version reply length: {size}")
    version = int.from_bytes(buffer[:2], "little")
    if not version:
        raise ValueError("Device did not report a UCFD protocol version.")
    return version


def query_identity(backend, handle):
    payload = exchange_command(backend, handle, INFO_OPCODE, response_size=IDENTITY.size)
    hardware, firmware, bootloader, model, can_ports, lin_ports = IDENTITY.unpack(payload)
    name = model.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return name, hardware, firmware, bootloader, can_ports, lin_ports


def encode_packet(opcode, channel, sequence, payload):
    packet = COMMAND.pack(0x55, opcode, channel, sequence, len(payload)) + payload
    return packet + crc16(packet)


def exchange_command(backend, handle, opcode, channel=0, payload=b"", response_size=0):
    # Commands use separate USB transfers for the header and data block.
    request = encode_packet(opcode, channel, 0, payload)
    for block in (request[:COMMAND.size], request[COMMAND.size:].ljust(1024, b"\x00")):
        if backend.bulk_write(handle, 0x01, 0, array("B", block), TIMEOUT_MS) != len(block):
            raise RuntimeError(f"Incomplete UCFD request 0x{opcode:02x}.")

    header = array("B", bytes(RESPONSE.size))
    if backend.bulk_read(handle, 0x81, 0, header, TIMEOUT_MS) != len(header):
        raise RuntimeError(f"Incomplete UCFD reply header for 0x{opcode:02x}.")
    tag, reply, extension, index, status, length = RESPONSE.unpack(header)
    # A rejected command still has a second transfer. Read it before checking
    # the status so it cannot be mistaken for the next command's header.
    buffer = array("B", bytes(1024))
    received = backend.bulk_read(handle, 0x81, 0, buffer, TIMEOUT_MS)
    if length > len(buffer) - 2 or received < length + 2:
        raise RuntimeError(f"UCFD 0x{opcode:02x} reply length mismatch: "
                           f"declared={length}, received={received}, status={status}.")
    payload = bytes(buffer[:length])
    if bytes(buffer[length:length + 2]) != crc16(bytes(header) + payload):
        raise RuntimeError(f"UCFD reply CRC mismatch for 0x{opcode:02x}.")
    if (tag, reply, extension, index, status, length) != (0x55, opcode, channel, 0, 0, response_size):
        raise RuntimeError(
            f"Unexpected UCFD reply for 0x{opcode:02x}: tag=0x{tag:02x}, opcode=0x{reply:02x}, "
            f"channel={extension}, sequence={index}, status={status}, length={length}"
        )
    return payload
