from fastapi import FastAPI, File
from typing import Annotated

import pytorrent

app = FastAPI()


@app.get("/progress")
async def get_progress() -> list[int]:
    return pytorrent.progress()


@app.post("/upload")
async def upload_torrent(file: Annotated[bytes, File()]):
    await pytorrent.download_file(file)
