from ..pytorrent import *
import asyncio
import unittest
import unittest.mock


class TestStreams(unittest.IsolatedAsyncioTestCase):
    async def test_handle_recv(self):        
        # Construct test message. Since our reader reads
        # the message in chunks, we have to mock sending it in chunks.
        # This seems to be a limitation of the mocking system, since
        # it is not necessary in the real world.
        msg_type = MessageType.REQUEST
        payload = b"Hello, Pytorrent"
        mock_writes = [
            int.to_bytes(len(payload) + 1, byteorder="big", length=4),
            int.to_bytes(msg_type) + payload
        ]

        # Seed mock with message
        mock_reader = unittest.mock.MagicMock(spec=asyncio.StreamReader)
        mock_reader.read = unittest.mock.AsyncMock(side_effect = mock_writes)
        # Assert that the received data matches the sent data
        received_data = await handle_recv(mock_reader)
        self.assertEqual(msg_type, received_data[0])
        self.assertEqual(payload, received_data[1:])

    async def test_request_piece(self):
        """
        This test makes two large simplifications:

        1. It uses a torrent file that's less than one piece block in size.
        2. It does not read what the client writes, but blindly responds
           with the first (and only) block of the first (and only) piece.
        """
        with open("./test/examples/ones.txt.torrent", "rb") as f:
            torrent_file = f.read()
        torrent = Torrent(decode_torrentfile(torrent_file))

        with open("./test/examples/ones.txt", "rb") as t:
            piece_data = t.read()
            
        piece_index = 0
        piece_begin = 0
        payload = struct.pack(">bII", MessageType.PIECE, piece_index, piece_begin) + piece_data
        mock_writes = [
            int.to_bytes(len(payload), byteorder="big", length=4),
            payload
        ]

        # Seed mock with message
        mock_reader = unittest.mock.MagicMock(spec=asyncio.StreamReader)
        mock_writer = unittest.mock.MagicMock(spec=asyncio.StreamWriter) # ignored
        mock_reader.read = unittest.mock.AsyncMock(side_effect = mock_writes)

        received_data = await request_piece(piece_index, torrent, mock_reader, mock_writer)
        self.assertEqual(received_data, piece_data)


class TestUtilities(unittest.TestCase):
    def test_extract_peer(self):
        peer = b'\x7f\x00\x00\x01\xc7\xd4'
        self.assertEqual(extract_peer(peer), ('127.0.0.1', 51156))
    
    def test_construct_peer_msg(self):
        sent = (MessageType.REQUEST, b"Msg")
        expected = b'\x00\x00\x00\x04\x06Msg'
        self.assertEqual(construct_peer_msg(*sent), expected)


if __name__ == "__main__":
    unittest.main()