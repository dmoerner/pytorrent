from fastapi import FastAPI, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import pytorrent

app = FastAPI()

manager = pytorrent.TorrentManager()

STATIC_DIR = "frontend/dist"

origins = [
    "http://localhost:5173", # local development server (svelte)
    "http://localhost:8000", # local development server (staticfiles)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

@app.get("/api/status/{info_hash}")
def get_status(info_hash: str) -> dict:
    response = manager.Get(info_hash)
    if response is None:
        raise HTTPException(status_code=404, detail="Torrent not found")
    print(response)
    return response

@app.post("/api/upload")
async def upload_torrent(background_tasks: BackgroundTasks, file: UploadFile):
    print("Received upload")

    if file.content_type != "application/x-bittorrent":
        raise HTTPException(status_code=415, detail="Unsupported file type")
    contents = await file.read()
    info_hash = manager.Add(contents)

    print("Background task starts here")

    background_tasks.add_task(manager.Start, info_hash)

    return JSONResponse(
        content={"info_hash": info_hash}
        )
