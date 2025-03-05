# Pytorrent

Pytorrent is a BitTorrent file downloader. This is a collaborative project
between Daniel Moerner and Lamone Armstrong, and is licensed under the MIT
License.

Implemented Features:

- All network traffic is implemented with `asyncio` to allow concurrent connections to multiple peers.
- Peers are selected based on a reputation score, which rewards fast peers and
  penalizes slow peers. We use a priority queue to organize peer scores, and a
  non-deterministic algorithm which both prefers high-scoring peers on average,
  but optimistically attempts to connect to new, unknown peers.
- Svelte Web frontend for easy uploading of `.torrent` files and viewing
  download progress.

Feature Roadmap:

- Support downloading of torrent files which consist of directories, rather
  than only single-file torrents.
- Support seeding torrents.
- Support starting and stopping torrents.
- Support multiple simultaneous downloads in the frontend.
