import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

DIRECTORY = Path(__file__).with_name(".data")
LOCK = Lock()


def _path(name):
    key = hashlib.sha256(name.strip().casefold().encode()).hexdigest()
    return DIRECTORY / f"{key}.json"


def load_profile(name):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"name": name, "last_topic": "", "history": []}


def remember(name, topic, result=None):
    with LOCK:
        profile = load_profile(name)
        profile["last_topic"] = topic
        if result is not None:
            profile["history"].append({"topic": topic, "score": result.score, "total": result.total})
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=DIRECTORY, delete=False) as file:
            temp = Path(file.name)
            json.dump(profile, file, ensure_ascii=False, indent=2)
        try:
            temp.replace(_path(name))
        finally:
            temp.unlink(missing_ok=True)
        return profile
