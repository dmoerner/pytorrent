import bencoder
import signal
import math
import socket
import struct
import random
import requests
from hashlib import sha1
from enum import IntEnum


def timeout(seconds=10, error_message="Timeout"):
    def decorator(func):
        def _handle_timeout(_, __):
            raise TimeoutError(error_message)

        def wrapper(*args, **kwargs):
            signal.signal(signal.SIGALRM, _handle_timeout)
            signal.alarm(seconds)
            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)
            return result

        return wrapper

    return decorator


class Message_Type(IntEnum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8


class BadHashError(Exception):
    pass


class ChokedError(Exception):
    pass


class TimeoutError(Exception):
    pass


def decode_torrentfile(filename: str) -> dict:
    """
    Given a torrent filename, return a bdecoded dict. Sample output:

    {b'announce': b'http://localhost:8080/announce',
    b'created by': b'mktorrent 1.1',
    b'creation date': 1737047340,
    b'info':
        {b'length': 12, b'name': b'data.txt',
        b'piece length': 262144,
        b'pieces': b'"Ycc\xb3\xde@\xb0o\x98\x1f\xb8]\x821.\x8c\x0e\xd5\x11',
        b'private': 1}
    }
    """
    with open(filename, "rb") as f:
        decoded = bencoder.decode(f.read())
        if not isinstance(decoded, dict):
            raise ValueError
        return decoded


def construct_announce(info: dict, port=6881) -> dict:
    """
    Given a torrent info dict and your port, construct the
    params for a tracker announce.
    """
    peer_id = b"ptc-0.1-" + random.randbytes(12)
    info_bencoded = bencoder.encode(info)
    info_hash = sha1(info_bencoded).digest()
    return {
        "info_hash": info_hash,
        "peer_id": peer_id,
        "port": port,
        "uploaded": 0,
        "downloaded": 0,
        "left": info[b"length"],
        "compact": 1,
    }


def handle_recv(s: socket.socket) -> bytes:
    """
    Read a length-prefixed message, ignoring keepalives, and return the message
    in bytes.
    """
    len_data = s.recv(4)
    length = int.from_bytes(len_data, byteorder="big")
    data = b""

    # Ignore keepalives
    while length == 0:
        print("DEBUG: recv keepalive")
        len_data = s.recv(4)
        length = int.from_bytes(len_data, byteorder="big")

    while len(data) < length:
        data += s.recv(length - len(data))
    return data


def construct_peer_msg(value_t: int, payload=b"") -> bytes:
    """
    Given a message type and a byte payload, return a length-prefixed
    message.
    """
    return (
        int.to_bytes(len(payload) + 1, byteorder="big", length=4)
        + int.to_bytes(value_t)
        + payload
    )


def wait_for_unchoke(s: socket.socket):
    """
    Peers start out in a choked state. Wait for a peer to unchoke.
    """
    data = handle_recv(s)
    while data[0] != Message_Type.UNCHOKE:
        print("DATA:", data)
        data = handle_recv(s)
    print("DEBUG: wait_for_unchoke, data=", data)


def handshake(params: dict, peer_ip: socket._Address, peer_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((peer_ip, peer_port))

    payload_header = int.to_bytes(19, byteorder="big") + b"BitTorrent protocol"
    payload = (
        payload_header + (int.to_bytes(0) * 8) + params["info_hash"] + params["peer_id"]
    )

    sock.sendall(payload)
    handshake = sock.recv(68)
    print("PEER RESPONSE:", handshake)
    handshake_header = handshake[:20]
    handshake_info_hash = handshake[28:48]

    assert payload_header == handshake_header
    assert params["info_hash"] == handshake_info_hash

    bitfield = handle_recv(sock)
    print("BITFIELD:", bitfield)

    interested = construct_peer_msg(Message_Type.INTERESTED)
    sock.sendall(interested)

    wait_for_unchoke(sock)

    return sock


def verify_piece_hash(torrent_info: dict, piece_hash: bytes, index: int) -> bool:
    """
    Given the hash of a downloaded piece, verify that it matches the hash in the
    torrent file.
    """
    print("PIECE HASH:", piece_hash, len(piece_hash))
    torrent_piece_hash = torrent_info[b"info"][b"pieces"][index * 20 : index * 20 + 20]
    print("TORRENT PIECE HASH:", torrent_piece_hash, len(torrent_piece_hash))
    return piece_hash == torrent_piece_hash


@timeout(30)
def request_piece(s: socket.socket, torrent_info: dict, index: int) -> bytes:
    """
    Request a piece from a peer. The order of messages is:

    us: handshake
    them: handshake
    them: bitfield
    us: interested
    them: choked or unchoked
    us: (wait for unchoked)
    us: request a piece
    them: (transfer data)
    """

    block_length = 2**14
    begin = 0
    piece_length = torrent_info[b"info"][b"piece length"]
    file_size = torrent_info[b"info"][b"length"]
    piece_count = math.ceil(file_size / piece_length)
    data_left = piece_length
    data = b""

    while data_left > 0:
        print(f"DEBUG: grabbing block {begin // 2**14} of piece {index}")
        if index == piece_count - 1 and data_left < block_length:
            block_length = data_left
        payload = struct.pack(">III", index, begin, block_length)
        request_payload = construct_peer_msg(Message_Type.REQUEST, payload)
        s.sendall(request_payload)
        requested_data = handle_recv(s)

        # loop until we get a piece type
        while requested_data[0] != b"x\07":
            if requested_data[0] == b"\x00":
                raise ChokedError
            requested_data = handle_recv(s)

        _, recv_index, recv_begin = struct.unpack(">cII", requested_data[:9])
        # assert recv_index == index, f"recv_index = {recv_index}"
        # assert recv_begin == begin, f"recv_begin = {recv_begin}"
        # if recv_type != b'\x07' or recv_index != index or recv_begin != begin:
        #     raise TimeoutError
        if recv_index != index or recv_begin != begin:
            raise TimeoutError
        data += requested_data[9:]
        begin += block_length
        data_left -= block_length

    if not verify_piece_hash(torrent_info, sha1(data).digest(), index):
        raise BadHashError
    return data


def request_file(
    params: dict, torrent_info: dict, peer_ip: socket._Address, peer_port: int
):
    """
    Request a file.
    """

    s = handshake(params, peer_ip, peer_port)
    data = b""
    piece_length = torrent_info[b"info"][b"piece length"]
    file_size = torrent_info[b"info"][b"length"]
    file_name = torrent_info[b"info"][b"name"].decode("utf8")
    piece_count = math.ceil(file_size / piece_length)
    index = 0
    # for index in range(piece_count):
    #     # catch: BadHash -> request_piece
    #     try:
    #         data += request_piece(s, torrent_info, index)
    #         # increment index
    #     except BadHashError:
    #         # retry requesting piece
    #         data += request_piece(s, torrent_info, index)
    #     except ChokedError:
    #         # choked -> run some unchoking waiting algorithm on s, then request_piece.
    #         wait_for_unchoke(s)
    #         data += request_piece
    #     except TimeoutError:
    #         # timeout error -> re-run handshake, update s to a new socket, then request_piece
    #         pass
    while index < piece_count:
        # catch: BadHash -> request_piece
        try:
            data += request_piece(s, torrent_info, index)
            print(f"finished piece: {index}")
            index += 1
            # increment index
        except BadHashError:
            # retry requesting piece
            print("Bad hash, restarting piece")
            continue
        except ChokedError:
            # choked -> run some unchoking waiting algorithm on s, then request_piece.
            wait_for_unchoke(s)
        except TimeoutError:
            # timeout error -> re-run handshake, update s to a new socket, then request_piece
            s = handshake(params, peer_ip, peer_port)

    with open(file_name, "wb") as f:
        f.write(data)


def main():
    print("Hello from torrentclient!")

    decoded_t = decode_torrentfile("./debian-12.9.0-amd64-netinst.iso.torrent")

    # Make announce
    tracker_url = decoded_t[b"announce"].decode("utf-8")
    params = construct_announce(decoded_t[b"info"])
    response = requests.get(tracker_url, params=params)

    # Decode peer list
    decoded_response = bencoder.decode(response.content)
    if not isinstance(decoded_response, dict):
        raise ValueError("Did not receive dict from tracker")
    peers = decoded_response[b"peers"]
    peers_list = [peers[i : i + 6] for i in range(0, len(peers), 6)]

    # Request File
    # first_peer = peers_list[0]
    # peer_ip = ipaddress.IPv4Address(first_peer[:4]).exploded
    # peer_port = int.from_bytes(first_peer[4:], byteorder="big")
    request_file(params, decoded_t, "93.161.53.57", 56251)


if __name__ == "__main__":
    main()
