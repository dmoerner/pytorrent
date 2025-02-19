import asyncio
import bencoder
import ipaddress
import logging
import math
import heapq
import random
import requests
import signal
import struct
import time

from hashlib import sha1
from enum import IntEnum
from typing import Tuple
from asyncio.streams import StreamReader, StreamWriter

logger = logging.getLogger(__name__)

def timeout(seconds=10, error_message="Timeout"):
    def decorator(func):
        def _handle_timeout(*_):
            raise TimeoutError(func)

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


class Torrent:
    def __init__(self, torrent_dict: dict, port=6881):
        self.torrent_dict = torrent_dict
        self.peer_id = b"ptc-0.1-" + random.randbytes(12)
        self.port = port
        self.torrent_info = torrent_dict[b"info"]
        self.info_hash = sha1(bencoder.encode(self.torrent_info)).digest()
        self.uploaded = 0
        self.downloaded = 0
        self.piece_length = self.torrent_info[b"piece length"]
        self.file_size = self.torrent_info[b"length"]
        self.piece_count = math.ceil(self.file_size / self.piece_length)
        # The peers list and heap must be protected by the peers lock.
        self.peers_lock = asyncio.Lock()
        self.peer_list = []
        self.peer_heap = []
        self.peers_socket_streams = {}
        # The amount left and the pieces completed are write protected
        # by the files lock and only updated when a piece is written to disk.
        self.file_lock = asyncio.Lock()
        self.left = self.file_size
        self.pieces = []

    async def Announce(self, event="empty", numwant=50, left=None):
        # Since default arguments are evaluated at function definition,
        # we cannot self-reference the left property.
        if left is None:
            left = self.left
        tracker_url = self.torrent_dict[b"announce"].decode("utf-8")
        params = {
            "info_hash": self.info_hash,
            "peer_id": self.peer_id,
            "port": self.port,
            "uploaded": self.uploaded,
            "downloaded": self.downloaded,
            "left": left,
            "compact": 1,
            "event": event,
            "numwant": numwant,
        }
        response = requests.get(tracker_url, params=params)
        decoded_response = bencoder.decode(response.content)
        if not isinstance(decoded_response, dict):
            raise ValueError("Did not receive dict from tracker")
        peers = decoded_response[b"peers"]
        peers_list = [peers[i : i + 6] for i in range(0, len(peers), 6)]
        async with self.peers_lock:
            for peer in peers_list:
                if peer not in self.peer_heap and peer not in self.peer_list:
                    self.peer_list.append(peer)

    async def Download(self):
        sem = asyncio.Semaphore(10)
        await self.Announce(event="started")

        async with asyncio.TaskGroup() as tg:
            # Only do 10 pieces for debugging:
            for piece_index in range(self.piece_count):
                tg.create_task(event_handler(piece_index, self, sem))
        logger.debug(f"All events processed.")

        await self.Announce(event="completed", numwant=0)


class TorrentManager:
    def __init__(self):
        self.torrents = {}

    def Add(self, torrent_data: bytes) -> Torrent:
        torrent_dict = decode_torrentfile(torrent_data)
        torrent = Torrent(torrent_dict)
        self.torrents[torrent.info_hash] = torrent
        return torrent

    def Get(self, infohash: bytes) -> dict:
        torrent = self.torrents[infohash]
        return {
            "torrent_dict": torrent.torrent_dict,
            "left": torrent.left,
            "pieces": torrent.pieces,
            "peer_heap": torrent.peer_heap,
            "uploaded": torrent.uploaded,
            "downloaded": torrent.downloaded,
        }


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


def decode_torrentfile(file: bytes) -> dict:
    """
    Given a torrent file in bytes, return a bdecoded dict. Sample output:

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
    decoded = bencoder.decode(file)
    if not isinstance(decoded, dict):
        raise ValueError
    return decoded


async def handle_recv(reader: StreamReader) -> bytes:
    """
    Read a length-prefixed message, ignoring keepalives, and return the message
    in bytes.
    """
    len_data = await reader.read(4)
    length = int.from_bytes(len_data, byteorder="big")
    data = b""

    if length == 0:
        logger.debug("DEBUG: recv keepalive, if this hangs, check line 117")

    # Ignore keepalives, up to a maximum of 5. Then move on to another peer.
    count = 0
    while length == 0:
        if count > 5:
            raise TimeoutError
        len_data = await reader.read(4)
        length = int.from_bytes(len_data, byteorder="big")
        count += 1

    while len(data) < length:
        # logger.debug("DEBUG: handle_recv. Waiting for all of the data...")
        data += await reader.read(length - len(data))
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


async def wait_for_unchoke(reader: StreamReader):
    """
    Peers start out in a choked state. Wait for a peer to unchoke.
    """
    data = await handle_recv(reader)
    while data[0] != Message_Type.UNCHOKE:
        logger.debug("CHOKED, DATA:", data)
        data = await handle_recv(reader)


@timeout(5)
async def handshake(
    peer_ip: str, peer_port: int, torrent: Torrent
) -> Tuple[StreamReader, StreamWriter]:
    """
    This function handshakes with a peer. It must have a timeout set, because
    if the peer's port is closed behind a firewall that DROPs packets instead
    of REJECTing them, the connection will just hang.
    """
    if (peer_ip, peer_port) in torrent.peers_socket_streams:
        return torrent.peers_socket_streams[(peer_ip, peer_port)]
    
    logger.debug(f"PEER_IP: {peer_ip}, PEER_PORT: {peer_port}")

    reader, writer = await asyncio.open_connection(peer_ip, peer_port)

    payload_header = int.to_bytes(19, byteorder="big") + b"BitTorrent protocol"
    payload = (
        payload_header + (int.to_bytes(0) * 8) + torrent.info_hash + torrent.peer_id
    )

    writer.write(payload)
    await writer.drain()

    handshake = await reader.read(68)
    logger.debug("PEER RESPONSE:", handshake)
    handshake_header = handshake[:20]
    handshake_info_hash = handshake[28:48]

    # AssertionError occurred - payload header = b'\x13BitTorrent protocol', handshake header = b''
    assert (
        payload_header == handshake_header
    ), f"payload header = {payload_header}, handshake header = {handshake_header}"
    assert (
        torrent.info_hash == handshake_info_hash
    ), f"info_hash = {torrent.info_hash} handshake info hash = {handshake_info_hash}"

    bitfield = await handle_recv(reader)
    _ = bitfield

    interested = construct_peer_msg(Message_Type.INTERESTED)
    writer.write(interested)
    await writer.drain()

    logger.debug("DEBUG: Waiting for unchoke from peer:", peer_ip)
    await wait_for_unchoke(reader)
    logger.debug("UNCHOKED by peer:", peer_ip)

    torrent.peers_socket_streams[(peer_ip, peer_port)] = (reader, writer)

    return reader, writer


def verify_piece_hash(piece_index: int, torrent: Torrent, piece_hash: bytes) -> bool:
    """
    Given the hash of a downloaded piece, verify that it matches the hash in the
    torrent file.
    """
    logger.debug("PIECE HASH:", piece_hash, len(piece_hash))
    torrent_piece_hash = torrent.torrent_info[b"pieces"][
        piece_index * 20 : piece_index * 20 + 20
    ]
    logger.debug("TORRENT PIECE HASH:", torrent_piece_hash, len(torrent_piece_hash))
    return piece_hash == torrent_piece_hash


async def _write_piece_to_disk(piece_index: int, torrent: Torrent, data: bytes):
    """
    Write a piece to disc. This uses write_lock.
    """
    piece_length = torrent.torrent_info[b"piece length"]
    output_file = torrent.torrent_info[b"name"]
    piece_start = piece_index * piece_length
    # For the last piece, this may not be equal to piece_length.
    piece_size = len(data)
    logger.debug(f"DEBUG: Writing piece {piece_index}")
    async with torrent.file_lock:
        with open(output_file, "w+b") as f:
            f.seek(piece_start)
            f.write(data)
            torrent.left -= piece_size
            torrent.downloaded += piece_size
            torrent.pieces.append(piece_index)
    logger.debug(f"DEBUG: Done writing piece {piece_index}")


async def download_piece(piece_index: int, torrent: Torrent):
    """
    Download piece.

    Uses an optimistic algorithm to either choose a new peer or to choose a
    seen peer from a priority queue which assigns peers scores a score
    dependent on their speed.
    """
    decision = random.choice(["heap", "list"])
    async with torrent.peers_lock:
        while not torrent.peer_list and not torrent.peer_heap:
            # We could sometimes do a torrent.Announce() to get more peers here.
            logger.debug("DEBUG: Waiting for a peer slot to open up")
            await asyncio.sleep(1)
        if torrent.peer_list and (
            decision == "list"
            or len(torrent.peer_heap) == 0
            or torrent.peer_heap[0][0] >= 0
        ):
            logger.debug("DEBUG: We chose the list!")
            peer = random.choice(torrent.peer_list)
            torrent.peer_list.remove(peer)
            score = 0
        else:
            logger.debug("DEBUG: We chose the heap! Current heap:", torrent.peer_heap)
            score, peer = heapq.heappop(torrent.peer_heap)
    try:
        start = time.time()
        data = await _request_piece(piece_index, torrent, peer)
        end = time.time()
        speed = len(data) // (end - start)
        async with torrent.peers_lock:
            heapq.heappush(torrent.peer_heap, (0 - speed, peer))
    except (ConnectionRefusedError, AssertionError) as e:
        ip, port = extract_peer(peer)
        logger.debug(f"DEBUG: Banned peer {ip}, {port}")
        raise e
    except TimeoutError as e:
        if e.args[0] is handshake:
            ip, port = extract_peer(peer)
            logger.debug(f"DEBUG: Banned peer {ip}, {port}")
            raise e
        else:
            ip, port = extract_peer(peer)
            logger.debug(f"DEBUG: Slow peer timed out, increased score: {ip}, {port}")
            async with torrent.peers_lock:
                heapq.heappush(torrent.peer_heap, (score + 1, peer))
            raise e
    except Exception as e:
        logger.debug(f"DEBUG: This is an unhandled exception: {e}")
        async with torrent.peers_lock:
            heapq.heappush(torrent.peer_heap, (score + 1, peer))
        raise e

    # logger.debug(f"DEBUG: download_piece: data = {data}")
    if not verify_piece_hash(piece_index, torrent, sha1(data).digest()):
        raise Exception("Could not verify piece hash")
    await _write_piece_to_disk(piece_index, torrent, data)
    return


async def event_handler(
    piece_index: int,
    torrent: Torrent,
    sem: asyncio.Semaphore,
    max_retries=10,
    timeout=5,
):
    async with sem:
        for attempt in range(max_retries):
            try:
                async with asyncio.timeout(timeout):
                    logger.debug(f"Piece Index: {piece_index}: Attempt {attempt + 1}")
                    await download_piece(piece_index, torrent)
                    logger.debug(f"Piece Index: {piece_index}: Success!")
                    return True
            except TimeoutError as e:
                logger.debug(f"Piece Index: {piece_index}: Timeout on attempt {attempt + 1}")
            except Exception as e:
                logger.debug(
                    f"Piece Index: {piece_index}: An error of type {type(e).__name__} occurred - {e}"
                )
        logger.debug(f"Piece Index: {piece_index}: Failed after {max_retries} attempts")
        return False


def extract_peer(peer: bytes) -> Tuple[str, int]:
    peer_ip = ipaddress.IPv4Address(peer[:4]).exploded
    peer_port = int.from_bytes(peer[4:], byteorder="big")
    return peer_ip, peer_port


@timeout(15)
async def _request_piece(piece_index: int, torrent: Torrent, peer: bytes) -> bytes:
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
    reader, writer = await handshake(peer_ip, peer_port, torrent)

    logger.debug(f"PEER_IP: {peer_ip}, PEER_PORT: {peer_port}, PIECE_INDEX: {piece_index}")

    block_length = 2**14
    begin = 0
    piece_length = torrent.torrent_info[b"piece length"]
    file_size = torrent.torrent_info[b"length"]
    piece_count = math.ceil(file_size / piece_length)
    data_left = piece_length
    data = b""

    while data_left > 0:
        logger.debug(f"DEBUG: grabbing block {begin // 2**14} of piece {piece_index}")
        if piece_index == piece_count - 1 and data_left < block_length:
            block_length = data_left
        payload = struct.pack(">III", piece_index, begin, block_length)
        request_payload = construct_peer_msg(Message_Type.REQUEST, payload)
        writer.write(request_payload)
        await writer.drain()
        requested_data = await handle_recv(reader)

        # loop until we get a piece type
        while requested_data[0] != Message_Type.PIECE:
            if requested_data[0] == Message_Type.CHOKE:
                await wait_for_unchoke(reader)
            requested_data = await handle_recv(reader)

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

    logger.debug("DEBUG: Request piece is about to return!")
    return data


async def main():
    logging.basicConfig(level=logging.DEBUG)
    logger.info("Hello from torrentclient!")

    manager = TorrentManager()

    with open("./debian-12.9.0-amd64-netinst.iso.torrent", "rb") as f:
        torrent = manager.Add(f.read())
        await torrent.Download()


if __name__ == "__main__":
    asyncio.run(main())
