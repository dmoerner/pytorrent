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


def decode_torrentfile(filename):
    with open(filename, "rb") as f:
        decoded = bencoder.decode(f.read())
        return decoded


def construct_get_request(info, peer_id, port=6881):
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


def handle_recv(s):
    """
    len = s.recv(4)
    do something to convert len into an int
    data = s.recv(len)
    return data
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


def handle_data(value_t, payload=b""):
    """2
    "\x00\x00\x01="
    "\x00\x00\x01=\x05\xff\xff\xff"
    """
    return (
        int.to_bytes(len(payload) + 1, byteorder="big", length=4)
        + int.to_bytes(value_t)
        + payload
    )


"""
torrent file: "length" means total size of data
so pieces count = length / piece length

BUT, in the messages that we send to a peer: "length" means the length
of a block of a piece.

{b'announce': b'http://localhost:8080/announce', 
b'created by': b'mktorrent 1.1', 
b'creation date': 1737047340, 
b'info': 
    {b'length': 12, b'name': b'data.txt', 
    b'piece length': 262144, 
    b'pieces': b'"Ycc\xb3\xde@\xb0o\x98\x1f\xb8]\x821.\x8c\x0e\xd5\x11', 
    b'private': 1}
}

index: 0, begin: 0, length: 12

other torrent file: piece length: 262_144, file_length: 300_000

while requesting piece 1: index 0, begin 0, begin 16384, begin 32768, etc. block_length: 2**14 = 16_384
while requesting piece 2: index 1, being length: 2**14, EXCEPT on the last block,
length would be 300000 % 2**14 = 5088 to get the remainder that's left.

begin = range(0, piece_length, block_length)

300_000 - 262_144

block_length = 2**14
begin = 0
piece = 0 # is actually what we are looping over in a higher for loop
data_left = piece_length
while data_left > 0:
    if piece == piece_count - 1 and data_left < block_length:
        block_length = data_left
    make request (piece, begin, block_length)
    begin += block_length
    data_left -= block_length

"""
decoded_t = decode_torrentfile("./debian-12.9.0-amd64-netinst.iso.torrent")
url = decoded_t[b"announce"].decode("utf-8")
# Making a GET request to the tracker
params = construct_get_request(decoded_t[b"info"], random.randbytes(20))
requests.packages.urllib3.util.connection.HAS_IPV6 = False

"""
response: b'd8:interval4:270012:min interval2:305:peers6:\x7f\x00\x00\x01\x1a\xe1e'
"""
response = requests.get(url, params=params)
decoded_response = bencoder.decode(response.content)
peers = decoded_response[b"peers"]
peers_list = [peers[i : i + 6] for i in range(0, len(peers), 6)]

# TODO: change later
# b'\x7f\x00\x00\x01\x1a\xe1
# first_peer = peers_list[0]
# peer_ip = ipaddress.IPv4Address(first_peer[:4]).exploded
# peer_port = int.from_bytes(first_peer[4:], byteorder="big")
# print("PEERS LIST: ",peers_list)
# print("PEER IP:", peer_ip, "PEER PORT:", peer_port)
# Connect to Peer
"""
1. Send handshake, receive handshake, verify handshake (verify protocol, verify info_hash)
2. Send a message: Unchoked
3. Download data
"""
# sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# PEER IP: 93.161.53.57 PEER PORT: 56251
# sock.connect((peer_ip, peer_port))
# sock.connect(("93.161.53.57", 56251))
# payload_header = int.to_bytes(19, byteorder="big") + b"BitTorrent protocol"
# payload = (
#     payload_header + (int.to_bytes(0) * 8) + params["info_hash"] + params["peer_id"]
# )


# sock.sendall(payload)
# handshake = sock.recv(68)
# print("PEER RESPONSE:", handshake)

# handshake_header = handshake[:20]
# handshake_info_hash = handshake[28:48]

# assert payload_header == handshake_header
# assert params["info_hash"] == handshake_info_hash


def wait_for_unchoke(s):
    data = handle_recv(s)
    while data[0] != Message_Type.UNCHOKE:
        print("DATA:", data)
        data = handle_recv(s)
    print("DEBUG: wait_for_unchoke, data=", data)


def handshake(peer_ip, peer_port):
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

    interested = handle_data(Message_Type.INTERESTED)
    sock.sendall(interested)

    # data = handle_recv(sock)

    # while data[0] != Message_Type.UNCHOKE:
    #     print("DATA:", data)
    #     data = handle_recv(sock)
    wait_for_unchoke(sock)

    return sock


"""
us: handshake
them: handshake
them: bitfield
us: interested
them: choked or unchoked
us: (wait for unchoked)
us: request a piece
them: (transfer data)

"""
# TODO: change hard coded values
# sock = handshake("93.161.53.57", 56251)

# interested = handle_data(Message_Type.INTERESTED)
# sock.sendall(interested)

# data = handle_recv(sock)

# while data[0] != Message_Type.UNCHOKE:
#     print("DATA:", data)
#     data = handle_recv(sock)

""""
  Piece Count: 2528
  Piece Size: 256.0 KiB
  Total Size: 662.7 MB

  request: piece 0
  we have nothing:
  request: index = 0, begin = 0, length = 2^14
  we get a block back
  request: index = 0 begin = 2^14, length = 2^14
  ...
"""
# begin = index = 0
# length = 2**14
# struct.pack('>III', index, begin, length)
# int.to_bytes(begin, length=4, byteorder='big') + int.to_bytes(index, length=4, byteorder='big') + int.to_bytes(length, length=4, byteorder='big')
# payload = struct.pack(">III", index, begin, length)
# request_payload = handle_data(Message_Type.REQUEST, payload)
# sock.sendall(request_payload)

# data = handle_recv(sock)
# print("REQUEST DATA:", data)

"""
aaaabbbb
piece size = 4 (bytes)
piece count = 2
"""

"""
while requesting piece 0: index 0, begin 0, begin 16384, begin 32768, etc. block_length: 2**14 = 16_384
while requesting piece 1: index 1, being length: 2**14, EXCEPT on the last block,
length would be 300000 % 2**14 = 5088 to get the remainder that's left.

begin = range(0, piece_length, block_length)

300_000 - 262_144

block_length = 2**14
begin = 0
index = 0 # is actually what we are looping over in a higher for loop
data_left = piece_length
while data_left > 0:
    if index == piece_count - 1 and data_left < block_length:
        block_length = data_left
    make request (piece, begin, block_length)
    begin += block_length
    data_left -= block_length

calculate piece hash
verify that the piece hash matches the hash in the torrent file, otherwise retry (or error)
the piece hash in the torrent file is a concatenation of hashes of each piece. so we 
"""


def verify_piece_hash(torrent_info, piece_hash, index):
    print("PIECE HASH:", piece_hash, len(piece_hash))
    torrent_piece_hash = torrent_info[b"info"][b"pieces"][index * 20 : index * 20 + 20]
    print("TORRENT PIECE HASH:", torrent_piece_hash, len(torrent_piece_hash))
    return piece_hash == torrent_piece_hash


@timeout(30)
def request_piece(s, torrent_info, index):
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
        request_payload = handle_data(Message_Type.REQUEST, payload)
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


def request_file(torrent_info, peer_ip, peer_port):
    s = handshake(peer_ip, peer_port)
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
            s = handshake(peer_ip, peer_port)

    with open(file_name, "w") as f:
        f.write(data)


# data = request_piece(sock, decoded_t, 0)
# print("REQUEST PIECE DATA:", data[:50])

# request_file(decoded_t, "93.161.53.57", 56251)


def main():
    print("Hello from torrentclient!")


if __name__ == "__main__":
    main()
