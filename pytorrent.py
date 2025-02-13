import asyncio
import bencoder
import ipaddress
import math
import heapq
import random
import requests
import signal
import socket
import struct
import time

from hashlib import sha1
from enum import IntEnum


write_lock = asyncio.Lock()


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
    info_hash = calc_info_hash(info)
    return {
        "info_hash": info_hash,
        "peer_id": peer_id,
        "port": port,
        "uploaded": 0,
        "downloaded": 0,
        "left": info[b"length"],
        "compact": 1,
    }


def calc_info_hash(info: dict) -> bytes:
    """
    Calculate an info_hash given an info dict from a torrent.
    """
    info_bencoded = bencoder.encode(info)
    return sha1(info_bencoded).digest()


def handle_recv(s: socket.socket) -> bytes:
    """
    Read a length-prefixed message, ignoring keepalives, and return the message
    in bytes.
    """
    len_data = s.recv(4)
    length = int.from_bytes(len_data, byteorder="big")
    data = b""

    if length == 0:
        print("DEBUG: recv keepalive, if this hangs, check line 117")

    # Ignore keepalives, up to a maximum of 5. Then move on to another peer.
    count = 0
    while length == 0:
        if count > 5:
            raise TimeoutError
        len_data = s.recv(4)
        length = int.from_bytes(len_data, byteorder="big")
        count += 1

    while len(data) < length:
        # print("DEBUG: handle_recv. Waiting for all of the data...")
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
        print("CHOKED, DATA:", data)
        data = handle_recv(s)
    # print("DEBUG: unchoked", data)


@timeout(5)
def handshake(peer_ip, peer_port: int, info_hash, peer_id) -> socket.socket:
    # "Bad Peer": PEER_IP: 99.240.48.62, PEER_PORT: 8773
    print(f"PEER_IP: {peer_ip}, PEER_PORT: {peer_port}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((peer_ip, peer_port))
    payload_header = int.to_bytes(19, byteorder="big") + b"BitTorrent protocol"
    payload = payload_header + (int.to_bytes(0) * 8) + info_hash + peer_id

    sock.sendall(payload)
    handshake = sock.recv(68)
    print("PEER RESPONSE:", handshake)
    handshake_header = handshake[:20]
    handshake_info_hash = handshake[28:48]

    # AssertionError occurred - payload header = b'\x13BitTorrent protocol', handshake header = b''
    assert payload_header == handshake_header, f"payload header = {payload_header}, handshake header = {handshake_header}"
    assert info_hash == handshake_info_hash, f"info_hash = {info_hash} handshake info hash = {handshake_info_hash}"

    bitfield = handle_recv(sock)
    # print("BITFIELD:", bitfield)

    interested = construct_peer_msg(Message_Type.INTERESTED)
    sock.sendall(interested)

    print("DEBUG: Waiting for unchoke from peer:", peer_ip)
    wait_for_unchoke(sock)
    print("UNCHOKED by peer:", peer_ip)
    return sock


def verify_piece_hash(torrent_info: dict, piece_hash: bytes, index: int) -> bool:
    """
    Given the hash of a downloaded piece, verify that it matches the hash in the
    torrent file.
    """
    print("PIECE HASH:", piece_hash, len(piece_hash))
    torrent_piece_hash = torrent_info[b"pieces"][index * 20 : index * 20 + 20]
    print("TORRENT PIECE HASH:", torrent_piece_hash, len(torrent_piece_hash))
    return piece_hash == torrent_piece_hash


async def _write_piece_to_disk(data: bytes, piece_index, torrent_info):
    """
    Write a piece to disc. This uses write_lock.
    """
    piece_length = torrent_info[b"piece length"]
    output_file = torrent_info[b"name"]
    piece_start = piece_index * piece_length
    print(f"DEBUG: Writing piece {piece_index}")
    async with write_lock:
        with open(output_file, "w+b") as f:
            f.seek(piece_start)
            f.write(data)
    print(f"DEBUG: Done writing piece {piece_index}")


async def download_piece(piece_index, torrent_info, peer_list, peer_heap, info_hash, peer_id):
    """
    Download piece.
    """
    decision = random.choice(["heap", "list"])
    if peer_list and (decision == "list" or len(peer_heap) == 0 or peer_heap[0][0] >= 0):
        print("DEBUG: We chose the list!")
        peer = random.choice(peer_list)
        peer_list.remove(peer)
        score = 0
    else:
        print("DEBUG: We chose the heap! Current heap:", peer_heap)
        score, peer = heapq.heappop(peer_heap)
    # This function is synchronous, but will contain timeout wrappers.
    try:
        start = time.time()
        data = _request_piece(piece_index, torrent_info, peer, info_hash, peer_id)
        end = time.time()
        speed = len(data) // (end - start)
        heapq.heappush(peer_heap, (0 - speed, peer))
    except Exception as e:
        heapq.heappush(peer_heap, (score + 1, peer))
        # delete peer from peer_list
        # peer_list.remove(peer)
        ip, port = extract_peer(peer)
        print(f"DEBUG: Banned peer {ip}, port {port}")
        raise e

    # print(f"DEBUG: download_piece: data = {data}")
    if not verify_piece_hash(torrent_info, sha1(data).digest(), piece_index):
        raise Exception("Could not verify piece hash")
    await _write_piece_to_disk(data, piece_index, torrent_info)
    return


async def event_handler(
    piece_index, torrent_info, peers_list, peers_heap, info_hash, peer_id, max_retries=3, timeout=5
):
    for attempt in range(max_retries):
        try:
            async with asyncio.timeout(timeout):
                print(f"Piece Index: {piece_index}: Attempt {attempt + 1}")
                await download_piece(
                    piece_index, torrent_info, peers_list, peers_heap, info_hash, peer_id
                )
                print(f"Piece Index: {piece_index}: Success!")
                return True
        except TimeoutError as e:
            print(f"Piece Index: {piece_index}: Timeout on attempt {attempt + 1}")
        except Exception as e:
            print(
                f"Piece Index: {piece_index}: An error of type {type(e).__name__} occurred - {e}"
            )
        await asyncio.sleep(1)  # Wait before retrying
    print(f"Piece Index: {piece_index}: Failed after {max_retries} attempts")
    return False


async def download_file(torrent_dict):
    tracker_url = torrent_dict[b"announce"].decode("utf-8")
    torrent_info = torrent_dict[b"info"]
    params = construct_announce(torrent_info)
    print("DEBUG: Contacting tracker")
    response = requests.get(tracker_url, params=params)

    # Decode peer list
    decoded_response = bencoder.decode(response.content)
    if not isinstance(decoded_response, dict):
        raise ValueError("Did not receive dict from tracker")
    peers = decoded_response[b"peers"]
    peers_list = [peers[i : i + 6] for i in range(0, len(peers), 6)]
    peers_heap = []

    print("DEBUG: Received list of length", len(peers_list))

    info_hash = params["info_hash"]
    peer_id = params["peer_id"]

    piece_length = torrent_info[b"piece length"]
    file_size = torrent_info[b"length"]
    piece_count = math.ceil(file_size / piece_length)

    async with asyncio.TaskGroup() as tg:
        # Only do 10 pieces for debugging:
        for piece_index in range(min(10, piece_count)):
            tg.create_task(
                event_handler(piece_index, torrent_info, peers_list, peers_heap, info_hash, peer_id)
            )
    print(f"All events processed.")

def extract_peer(peer):
    peer_ip = ipaddress.IPv4Address(peer[:4]).exploded
    peer_port = int.from_bytes(peer[4:], byteorder="big")
    return peer_ip, peer_port

@timeout(15)
def _request_piece(piece_index, torrent_info: dict, peer, info_hash, peer_id) -> bytes:
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
    peer_ip, peer_port = extract_peer(peer)
    s = handshake(peer_ip, peer_port, info_hash, peer_id)

    print(f"PEER_IP: {peer_ip}, PEER_PORT: {peer_port}, PIECE_INDEX: {piece_index}")

    block_length = 2**14
    begin = 0
    piece_length = torrent_info[b"piece length"]
    file_size = torrent_info[b"length"]
    piece_count = math.ceil(file_size / piece_length)
    data_left = piece_length
    data = b""

    while data_left > 0:
        print(f"DEBUG: grabbing block {begin // 2**14} of piece {piece_index}")
        if piece_index == piece_count - 1 and data_left < block_length:
            block_length = data_left
        payload = struct.pack(">III", piece_index, begin, block_length)
        request_payload = construct_peer_msg(Message_Type.REQUEST, payload)
        s.sendall(request_payload)
        requested_data = handle_recv(s)

        # loop until we get a piece type
        while requested_data[0] != Message_Type.PIECE:
            if requested_data[0] == Message_Type.CHOKE:
                wait_for_unchoke(s)
            requested_data = handle_recv(s)

        _, recv_index, recv_begin = struct.unpack(">cII", requested_data[:9])
        # assert recv_index == index, f"recv_index = {recv_index}"
        # assert recv_begin == begin, f"recv_begin = {recv_begin}"
        # if recv_type != b'\x07' or recv_index != index or recv_begin != begin:
        #     raise TimeoutError
        if recv_index != piece_index or recv_begin != begin:
            raise TimeoutError
        data += requested_data[9:]
        begin += block_length
        data_left -= block_length

    print("DEBUG: Request piece is about to return!")
    return data


async def main():
    print("Hello from torrentclient!")

    decoded_t = decode_torrentfile("./debian-12.9.0-amd64-netinst.iso.torrent")
    await download_file(decoded_t)


if __name__ == "__main__":
    asyncio.run(main())
