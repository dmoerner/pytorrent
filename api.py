from fastapi import FastAPI, File
from fastapi.staticfiles import StaticFiles
from typing import Annotated

import pytorrent

app = FastAPI()

manager = pytorrent.TorrentManager()
torrents = dict()

STATIC_DIR = "frontend/dist"

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

@app.get("/progress/{info_hashhex}")
async def get_progress(info_hashhex: str) -> list[int]:
    return torrents[info_hashhex].Get()


@app.post("/upload")
async def upload_torrent(file: Annotated[bytes, File()]):
    torrent = manager.Add(file)
    torrents[torrent.info_hashhex] = torrent
    await torrent.Download()
