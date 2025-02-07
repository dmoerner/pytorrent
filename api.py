from flask import Flask, jsonify, request

import pytorrent

app = Flask(__name__)


@app.route("/progress")
def get_progress():
    return jsonify(pytorrent.progress())


@app.route("/upload", methods=["POST"])
def upload_torrent():
    if "file" not in request.files:
        return jsonify({"message": "No file uploaded"}), 400
    file = request.files["file"]

    # This needs to be modified to be non-blocking.
    pytorrent.download_file(file)

    return jsonify({"message": "File uploaded"})
