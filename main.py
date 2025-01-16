import bencoder
import ipaddress
import socket
import re
import random
import requests
from hashlib import sha1

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
        "left": info[b'length']
    }

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
decoded_t = decode_torrentfile("./data/data.txt.torrent")
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
print("PEER IP:", peer_ip, "PEER PORT:", peer_port)
# socket stuff
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((peer_ip, peer_port))

def main():
    print("Hello from torrentclient!")


if __name__ == "__main__":
    main()
