import bencoder
import ipaddress
import socket
import struct
import re
import random
import requests
from hashlib import sha1
from enum import IntEnum

class Message_Type(IntEnum):
    CHOKE           = 0
    UNCHOKE         = 1
    INTERESTED      = 2
    NOT_INTERESTED  = 3
    HAVE            = 4
    BITFIELD        = 5
    REQUEST         = 6
    PIECE           = 7 
    CANCEL          = 8

def decode_torrentfile(filename):
    with open(filename, 'rb') as f:
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
        "left": info[b'length'],
        "compact": 1
    }

def handle_recv(s):
    """
    len = s.recv(4)
    do something to convert len into an int
    data = s.recv(len)
    return data
    """
    len_data = s.recv(4)
    length = int.from_bytes(len_data, byteorder='big')
    data = b''

    # Ignore keepalives
    while length == 0:
        print("DEBUG: recv keepalive")
        len_data = s.recv(4)
        length = int.from_bytes(len_data, byteorder='big')

    while len(data) < length:
        data += s.recv(length - len(data))
    return data

def handle_data(value_t, payload=b''): 
    """2
    "\x00\x00\x01="
    "\x00\x00\x01=\x05\xff\xff\xff"
    """
    return int.to_bytes(len(payload) + 1, byteorder='big', length=4) + int.to_bytes(value_t) + payload

"""
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
decoded_t = decode_torrentfile("./debian-12.9.0-amd64-netinst.iso.torrent")
url = decoded_t[b'announce'].decode('utf-8')
# Making a GET request to the tracker
params = construct_get_request(decoded_t[b'info'], random.randbytes(20))
requests.packages.urllib3.util.connection.HAS_IPV6 = False

"""
response: b'd8:interval4:270012:min interval2:305:peers6:\x7f\x00\x00\x01\x1a\xe1e'
"""
response = requests.get(url, params=params)
decoded_response = bencoder.decode(response.content)
peers = decoded_response[b'peers']
peers_list = [peers[i: i + 6] for i in range(0, len(peers), 6)]

# TODO: change later
# b'\x7f\x00\x00\x01\x1a\xe1
first_peer = peers_list[0]
peer_ip = ipaddress.IPv4Address(first_peer[:4]).exploded
peer_port = int.from_bytes(first_peer[4:], byteorder="big")
# print("PEERS LIST: ",peers_list)
print("PEER IP:", peer_ip, "PEER PORT:", peer_port)
# Connect to Peer
"""
1. Send handshake, receive handshake, verify handshake (verify protocol, verify info_hash)
2. Send a message: Unchoked
3. Download data
"""
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# PEER IP: 93.161.53.57 PEER PORT: 56251
# sock.connect((peer_ip, peer_port))
sock.connect(("93.161.53.57", 56251))
payload_header = int.to_bytes(19, byteorder='big') + b'BitTorrent protocol'
payload = payload_header + (int.to_bytes(0) * 8) + params["info_hash"] + params["peer_id"]


sock.sendall(payload)
handshake = sock.recv(68)
print("PEER RESPONSE:", handshake)

handshake_header = handshake[:20]
handshake_info_hash = handshake[28:48]

assert payload_header == handshake_header
assert params["info_hash"] == handshake_info_hash

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

bitfield = handle_recv(sock)

print("BITFIELD:", bitfield)

interested = handle_data(Message_Type.INTERESTED)
sock.sendall(interested)

data = handle_recv(sock)

# while handle_recv(sock)[0] != int.to_bytes(Message_Type.UNCHOKE):
#     pass
while data[0] != Message_Type.UNCHOKE:
    print("DATA:", data)
    data = handle_recv(sock)

print("Terminated")

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
begin = index = 0
length = 2**14
# struct.pack('>III', index, begin, length)
#int.to_bytes(begin, length=4, byteorder='big') + int.to_bytes(index, length=4, byteorder='big') + int.to_bytes(length, length=4, byteorder='big')
payload = struct.pack('>III', index, begin, length)
request_payload = handle_data(Message_Type.REQUEST, payload)
sock.sendall(request_payload)

data = handle_recv(sock)
print("REQUEST DATA:", data)

"""
aaaabbbb
piece size = 4 (bytes)
piece count = 2
"""

def main():
    print("Hello from torrentclient!")


if __name__ == "__main__":
    main()
