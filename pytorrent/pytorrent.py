import asyncio
import bencoder
import heapq
import ipaddress
import logging
import math
import random
import requests
import signal
import struct
import sys
import time

from asyncio.streams import StreamReader, StreamWriter
from hashlib import sha1
from enum import IntEnum
from typing import Tuple

logger = logging.getLogger(__name__)

# WORKER_COUNT limits the number of concurrent tasks, controlled
# by a semaphore.
WORKER_COUNT = 10

def timeout(seconds=10):
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
    """
    The Torrent class is the main object we manipulate. Encapsulated in it are
    information about the torrent from the torrent file, information about the
    current download progress, and locks.
    """
    def __init__(self, torrent_dict: dict, port=6881):
        # Information about our client
        self.peer_id = b"ptc-0.1-" + random.randbytes(12)
        self.port = port


        # Information from torrent file
        self.torrent_dict = torrent_dict
        self.torrent_info = torrent_dict[b"info"]
        self.info_hash = sha1(bencoder.encode(self.torrent_info)).digest()
        self.info_hashhex = sha1(bencoder.encode(self.torrent_info)).hexdigest()
        self.piece_length = self.torrent_info[b"piece length"]
        self.multifile = b"length" not in self.torrent_info
        if self.multifile:
            self.file_size = sum(file[b"length"] for file in self.torrent_info[b"files"])
        else:
            self.file_size = self.torrent_info[b"length"]
        self.piece_count = math.ceil(self.file_size / self.piece_length)


        # Download properties, including locks.
        self.tg = asyncio.TaskGroup()
        self.uploaded = 0
        self.downloaded = 0

        # The amount left and the pieces completed are write protected
        # by the files lock and only updated when a piece is written to disk.
        self.file_lock = asyncio.Lock()
        self.left = self.file_size
        self.pieces: set[int] = set()

        # The peers list and heap must be protected by the peers lock.
        self.peers_lock = asyncio.Lock()
        self.peer_list: list[bytes] = []
        self.peer_heap: list[tuple[int, bytes]] = []
        self.peers_socket_streams: dict[tuple[str, int], tuple[StreamReader, StreamWriter]] = {}


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
            logger.info(f"Peer list: {self.peer_list}")


    async def Start(self):
        sem = asyncio.Semaphore(WORKER_COUNT)
        await self.Announce(event="started")

        async with self.tg:
            try:
                for piece_index in range(self.piece_count):
                    if piece_index not in self.pieces:
                        self.tg.create_task(event_handler(piece_index, self, sem))
            except asyncio.CancelledError:
                logger.info("Torrent stopped")
            finally:
                await self.closeConnections()
        logger.info("All events processed.")

        await self.Announce(event="completed", numwant=0)

    async def Stop(self):
        """
        Cancel tasks (which closes connections), clear peer lists,
        reset the task group, and send a clean-up announce.
        """
        for task in self.tg._tasks:
            task.cancel()
        self.peer_heap = []
        self.peer_list = []
        self.tg = asyncio.TaskGroup()
        await self.Announce(event="stopped", numwant=0)


    async def closeConnections(self):
        for _, writer in self.peers_socket_streams.values():
            writer.close()
            await writer.wait_closed()
        self.peers_socket_streams = {}

class TorrentManager:
    """
    The TorrentManager class keeps a dictionary of current
    torrent objects, and provides a method to get information about
    them.

    Currently only a single download is supported, but this Manager
    is still a useful abstraction to get progress on that download.
    """
    def __init__(self):
        self.torrents = {}

    def Add(self, torrent_data: bytes) -> str:
        torrent_dict = decode_torrentfile(torrent_data)
        torrent = Torrent(torrent_dict)
        self.torrents[torrent.info_hashhex] = torrent
        return torrent.info_hashhex

    async def Start(self, info_hash: str):
        await self.torrents[info_hash].Start()

    async def Stop(self, info_hash: str):
        await self.torrents[info_hash].Stop()

    def Search(self, info_hash: str) -> bool:
        return info_hash in self.torrents

    def Get(self, info_hash: str) -> dict:
        """
        Get is only called after a successful search.
        """
        torrent = self.torrents[info_hash]
        response = {
            "torrent_name": torrent.torrent_info[b"name"].decode(),
            "size": torrent.file_size,
            "left": torrent.left,
            "pieces": list(torrent.pieces),
            "piece_count": torrent.piece_count,
            "uploaded": torrent.uploaded,
            "downloaded": torrent.downloaded,
        }
        return response


class MessageType(IntEnum):
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
    Given a torrent file in bytes, return a bdecoded dict. Sample output
    for single file torrent:

    {b'announce': b'http://localhost:8080/announce',
    b'created by': b'mktorrent 1.1',
    b'creation date': 1737047340,
    b'info':
        {b'length': 12, b'name': b'data.txt',
        b'piece length': 262144,
        b'pieces': b'"Ycc\xb3\xde@\xb0o\x98\x1f\xb8]\x821.\x8c\x0e\xd5\x11',
        b'private': 1}
    }

    Sample output for multi-file torrent:

    {b'created by': b'mktorrent 1.1',
     b'creation date': 1742305570,
     b'info': {b'files': [{b'length': 2, b'path': [b'1.txt']},
                          {b'length': 2, b'path': [b'subdir', b'2.txt']},
                          {b'length': 2, b'path': [b'subdir', b'5.txt']},
                          {b'length': 2,
                           b'path': [b'subdir', b'subsubdir', b'3.txt']},
                          {b'length': 2,
                           b'path': [b'subdir', b'subsubdir', b'4.txt']}],
               b'name': b'torrentdir',
               b'piece length': 262144,
               b'pieces': b'\x00\xa6\x90\xbb\x94we\x0e\xab\x11\xce\xfa'
                          b'\x0f\x08\xd8\xab"\xa4\xf2\x01'}}
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
        # Data may be buffered, so make sure we collect it all.
        data += await reader.read(length - len(data))
    return data


def construct_peer_msg(value_t: MessageType, payload=b"") -> bytes:
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
    while data[0] != MessageType.UNCHOKE:
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

    assert (
        payload_header == handshake_header
    ), f"payload header = {payload_header!r}, handshake header = {handshake_header!r}"
    assert (
        torrent.info_hash == handshake_info_hash
    ), f"info_hash = {torrent.info_hash!r} handshake info hash = {handshake_info_hash!r}"

    bitfield = await handle_recv(reader)
    _ = bitfield

    interested = construct_peer_msg(MessageType.INTERESTED)
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


async def write_piece_to_disk(piece_index: int, torrent: Torrent, data: bytes):
    """
    Write a piece to disc. Dispatch to appropriate helper function.
    """
    if torrent.multifile:
        await write_piece_multifile(piece_index, torrent, data)
    else:
        await write_piece_singlefile(piece_index, torrent, data)


async def write_piece_singlefile(piece_index: int, torrent: Torrent, data: bytes):
    """
    Write to a single-file torrent. This holds a lock on the file.
    """
    piece_length = torrent.torrent_info[b"piece length"]
    output_file = torrent.torrent_info[b"name"]
    piece_start = piece_index * piece_length
    # For the last piece, this may not be equal to piece_length.
    piece_size = len(data)
    logger.info(f"DEBUG: Writing piece {piece_index}")
    async with torrent.file_lock:
        with open(output_file, "w+b") as f:
            f.seek(piece_start)
            f.write(data)
            torrent.left -= piece_size
            torrent.downloaded += piece_size
            torrent.pieces.add(piece_index)
    logger.debug(f"DEBUG: Done writing piece {piece_index}")

async def write_piece_multifile(piece_index: int, torrent: Torrent, data: bytes):
    """
    Torrents may have multiple files organized in directories. Aligning files on
    piece boundaries is only optional and cannot be assumed. Therefore, to
    determine which file to write, we must index into the correct location in
    the files dict. We must close files that are finished, and seek to the
    correct location in files that were started in a previous piece. We must
    also create any directories.

    For now, this will probably be implemented with a single lock for the entire
    torrent, but it should be possible to move to per-file locks.
    """
    pass


async def download_piece(piece_index: int, torrent: Torrent):
    """
    Download piece.

    Uses an optimistic algorithm to either choose a new peer or to choose a
    seen peer from a priority queue which assigns peers scores a score
    dependent on their speed.
    """
    decision = random.choices(["heap", "list"], weights=[80, 20])
    async with torrent.peers_lock:
        while not torrent.peer_list and not torrent.peer_heap:
            # We could sometimes do a torrent.Announce() to get more peers here.
            logger.info("DEBUG: Waiting for a peer slot to open up")
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

        peer_ip, peer_port = extract_peer(peer)
        reader, writer = await handshake(peer_ip, peer_port, torrent)
        logger.info(f"PEER_IP: {peer_ip}, PEER_PORT: {peer_port}, PIECE_INDEX: {piece_index}")

        data = await request_piece(piece_index, torrent, reader, writer)
        end = time.time()
        speed = len(data) // int(end - start)
        ip, port = extract_peer(peer)
        logger.info(f"DEBUG: Good peer {ip}, {port}")
        async with torrent.peers_lock:
            heapq.heappush(torrent.peer_heap, (0 - speed, peer))
    except TimeoutError as e:
        # TimeoutError is a subset of OSError, so we must handle this first.
        if e.args[0] is handshake:
            ip, port = extract_peer(peer)
            logger.info(f"DEBUG: Banned peer for Handshaked Timeout {ip}, {port}")
            raise e
        else:
            ip, port = extract_peer(peer)
            logger.info(f"DEBUG: Slow peer timed out, increased score: {ip}, {port}")
            async with torrent.peers_lock:
                heapq.heappush(torrent.peer_heap, (score + 1, peer))
            raise e
    except (ConnectionRefusedError, ConnectionResetError, AssertionError, OSError) as e:
        ip, port = extract_peer(peer)
        logger.info(f"DEBUG: Banned peer for ConnectionRefused or Assertion Error {ip}, {port}")
        raise e
    except Exception as e:
        logger.warning(f"DEBUG: PIECE {piece_index} This is an unhandled exception when scoring peers: {e} {type(e)}")
        async with torrent.peers_lock:
            heapq.heappush(torrent.peer_heap, (score + 1, peer))
        raise e

    if not verify_piece_hash(piece_index, torrent, sha1(data).digest()):
        raise Exception("Could not verify piece hash")
    await write_piece_to_disk(piece_index, torrent, data)
    return


async def event_handler(
    piece_index: int,
    torrent: Torrent,
    sem: asyncio.Semaphore,
    max_retries=10,
):
    """
    This is the dispatcher, with retries, for each piece. It must hold a semaphore.
    """
    async with sem:
        for attempt in range(max_retries):
            try:
                logger.debug(f"Piece Index: {piece_index}: Attempt {attempt + 1}")
                await download_piece(piece_index, torrent)
                logger.debug(f"Piece Index: {piece_index}: Success!")
                return True
            except TimeoutError:
                logger.debug(f"Piece Index: {piece_index}: Timeout on attempt {attempt + 1}")
            except asyncio.CancelledError:
                raise
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
async def request_piece(piece_index: int, torrent: Torrent, reader: StreamReader, writer: StreamWriter) -> bytes:
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
    # peer_ip, peer_port = extract_peer(peer)
    # reader, writer = await handshake(peer_ip, peer_port, torrent)

    # logger.info(f"PEER_IP: {peer_ip}, PEER_PORT: {peer_port}, PIECE_INDEX: {piece_index}")

    block_length = 2**14
    begin = 0
    piece_length = torrent.torrent_info[b"piece length"]
    file_size = torrent.torrent_info[b"length"]
    piece_count = math.ceil(file_size / piece_length)
    data_left = piece_length
    if piece_index == piece_count - 1:
        data_left = torrent.file_size % piece_length
    data = b""

    while data_left > 0:
        logger.debug(f"DEBUG: grabbing block {begin // 2**14} of piece {piece_index}")
        if piece_index == piece_count - 1 and data_left < block_length:
            block_length = data_left
        payload = struct.pack(">III", piece_index, begin, block_length)
        request_payload = construct_peer_msg(MessageType.REQUEST, payload)
        writer.write(request_payload)
        await writer.drain()
        requested_data = await handle_recv(reader)

        # loop until we get a piece type
        while requested_data[0] != MessageType.PIECE:
            if requested_data[0] == MessageType.CHOKE:
                await wait_for_unchoke(reader)
            requested_data = await handle_recv(reader)

        _, recv_index, recv_begin = struct.unpack(">cII", requested_data[:9])
        if recv_index != piece_index or recv_begin != begin:
            raise TimeoutError
        data += requested_data[9:]
        begin += block_length
        data_left -= block_length

    logger.debug("DEBUG: Request piece is about to return!")
    return data


async def main():
    logging.basicConfig(level=logging.INFO)
    logger.debug("Hello from torrentclient!")

    if len(sys.argv) != 2:
        logger.error(f"Usage: {sys.argv[0]} torrentfile.torrent")
        sys.exit(1)

    manager = TorrentManager()

    torrentfile = sys.argv[1]

    with open(torrentfile, "rb") as f:
        info_hash = manager.Add(f.read())
        await manager.Start(info_hash)


if __name__ == "__main__":
    asyncio.run(main())
