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
- Svelte Web front-end for easy uploading of `.torrent` files and viewing
  download progress. Includes a dynamically-generated SVG status bar to track each
  piece as it completes.

Feature Roadmap:

- Support downloading of torrent files which consist of directories, rather
  than only single-file torrents.
- Support seeding torrents.
- Support starting and stopping torrents.
- Support multiple simultaneous downloads in the front-end.
- Simplify installation and deployment.

# Installation

Pytorrent is currently in pre-alpha stage and simplifying installation is on
the roadmap. It consists of a Python FastAPI backend and a Svelte web app
frontend. There is currently only support for running a development build.

Pytorrent requires `uv` (for Python) and `npm` (for Svelte)

First, clone this repository and start the backend:

```bash
 $ git clone https://github.com/dmoerner/pytorrent && cd pytorrent
 $ uv run fastapi dev
```

Then, in another terminal enter the frontend "svelte" directory and start the frontend:

```bash
$ cd svelte
$ npm run dev
```

The application will now be available on `http://localhost:5173`
