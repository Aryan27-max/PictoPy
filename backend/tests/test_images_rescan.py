"""
The watcher calls sync-folder on any file change, so the folder walk reruns
constantly over folders that are almost entirely unchanged. These cover the
check that keeps it from rereading and re-thumbnailing every file each time.
"""

import json
import os
import sqlite3
import tempfile
from typing import Any, Dict, Iterator, List

import pytest
from PIL import Image

from app.database.folders import db_create_folders_table
from app.database.images import (
    db_bulk_insert_images,
    db_create_images_table,
    db_get_image_sync_state_by_folder_ids,
)
from app.database.memories import (
    db_create_memories_table,
    db_get_memory,
    db_list_memories,
    db_upsert_memory,
)
from app.database.metadata import db_create_metadata_table
from app.database.videos import db_create_videos_table
from app.database.yolo_mapping import db_create_YOLO_classes_table
from app.utils.images import (
    image_util_extract_metadata,
    image_util_generate_thumbnail,
    image_util_is_unchanged,
    image_util_prepare_image_records,
    image_util_process_folder_images,
)


@pytest.fixture(scope="function")
def test_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the image/folder DB modules at a fresh tempfile database."""
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)

    monkeypatch.setattr("app.config.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.database.images.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.database.folders.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.database.yolo_mapping.DATABASE_PATH", db_path)
    monkeypatch.setattr("app.database.videos.DATABASE_PATH", db_path)
    # Dropping obsolete images now prunes the memories they emptied, which
    # reads the minimum out of the metadata blob.
    monkeypatch.setattr("app.database.metadata.DATABASE_PATH", db_path)

    db_create_YOLO_classes_table()  # mappings (image_classes FK target)
    db_create_folders_table()  # folders (images.folder_id FK target)
    db_create_images_table()
    db_create_videos_table()  # memory_videos FK target
    db_create_metadata_table()
    db_create_memories_table()

    yield db_path

    os.unlink(db_path)


@pytest.fixture
def folder(test_db: str) -> str:
    """Insert a folder row to satisfy the images.folder_id FK."""
    conn = sqlite3.connect(test_db)
    conn.execute(
        "INSERT INTO folders (folder_id, folder_path, last_modified_time, AI_Tagging) "
        "VALUES (?, ?, 0, 1)",
        ("folder-1", "/photos"),
    )
    conn.commit()
    conn.close()
    return "folder-1"


def _photo(path, colour=(10, 20, 30)) -> str:
    """Write a real JPEG; the walk opens and verifies every candidate file."""
    Image.new("RGB", (8, 8), colour).save(path, "JPEG")
    return str(path)


def _thumbnail(path) -> str:
    Image.new("RGB", (4, 4), "white").save(path, "JPEG")
    return str(path)


def _state(photo: str, thumbnail: str, **overrides: Any) -> Dict[str, Any]:
    """The sync state a previous scan would have recorded for an untouched file."""
    stats = os.stat(photo)
    recorded: Dict[str, Any] = {
        "thumbnailPath": thumbnail,
        "file_size": stats.st_size,
        "file_mtime": int(stats.st_mtime),
    }
    recorded.update(overrides)
    return recorded


class TestIsUnchanged:
    def test_matching_size_and_mtime_is_unchanged(self, tmp_path):
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "thumb.jpg")
        assert image_util_is_unchanged(photo, _state(photo, thumb)) is True

    def test_a_rewritten_file_is_changed(self, tmp_path):
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "thumb.jpg")
        recorded = _state(photo, thumb)

        os.utime(photo, (0, 0))

        assert image_util_is_unchanged(photo, recorded) is False

    def test_a_resized_file_is_changed(self, tmp_path):
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "thumb.jpg")
        recorded = _state(photo, thumb, file_size=999999)
        assert image_util_is_unchanged(photo, recorded) is False

    def test_a_missing_thumbnail_forces_a_reread(self, tmp_path):
        """Skipping means keeping the old thumbnail, so it has to still exist."""
        photo = _photo(tmp_path / "a.jpg")
        recorded = _state(photo, str(tmp_path / "gone.jpg"))
        assert image_util_is_unchanged(photo, recorded) is False

    def test_a_half_written_thumbnail_forces_a_reread(self, tmp_path):
        photo = _photo(tmp_path / "a.jpg")
        truncated = tmp_path / "thumb.jpg"
        truncated.write_bytes(b"")
        assert image_util_is_unchanged(photo, _state(photo, str(truncated))) is False

    def test_a_row_predating_mtime_tracking_is_reread(self, tmp_path):
        """Shipped databases have file_size but no file_mtime; reread them once."""
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "thumb.jpg")
        assert (
            image_util_is_unchanged(photo, _state(photo, thumb, file_mtime=None))
            is False
        )

    def test_an_unknown_path_is_changed(self, tmp_path):
        assert image_util_is_unchanged(str(tmp_path / "a.jpg"), None) is False

    def test_a_file_that_vanished_is_changed(self, tmp_path):
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "thumb.jpg")
        recorded = _state(photo, thumb)
        os.unlink(photo)
        assert image_util_is_unchanged(photo, recorded) is False


class TestPrepareImageRecordsSkips:
    """Regenerating a thumbnail per file is the cost this avoids."""

    @pytest.fixture
    def thumbnail_dir(self, tmp_path, monkeypatch) -> str:
        target = tmp_path / "thumbs"
        target.mkdir()
        monkeypatch.setattr("app.utils.images.THUMBNAIL_IMAGES_PATH", str(target))
        return str(target)

    @pytest.fixture
    def counted_thumbnails(self, monkeypatch) -> List[str]:
        """Record every file the run actually regenerated a thumbnail for."""
        generated: List[str] = []

        def spy(image_path: str, thumbnail_path: str, *args, **kwargs):
            generated.append(image_path)
            return image_util_generate_thumbnail(
                image_path, thumbnail_path, *args, **kwargs
            )

        monkeypatch.setattr("app.utils.images.image_util_generate_thumbnail", spy)
        return generated

    def test_an_unchanged_file_produces_no_record_and_no_thumbnail(
        self, tmp_path, thumbnail_dir, counted_thumbnails
    ):
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "existing.jpg")
        known = {os.path.normcase(os.path.abspath(photo)): _state(photo, thumb)}

        records = image_util_prepare_image_records(
            [photo], {str(tmp_path): "folder-1"}, known
        )

        assert records == []
        assert counted_thumbnails == []

    def test_a_changed_file_is_still_processed(
        self, tmp_path, thumbnail_dir, counted_thumbnails
    ):
        photo = _photo(tmp_path / "a.jpg")
        thumb = _thumbnail(tmp_path / "existing.jpg")
        known = {
            os.path.normcase(os.path.abspath(photo)): _state(
                photo, thumb, file_size=12345
            )
        }

        records = image_util_prepare_image_records(
            [photo], {str(tmp_path): "folder-1"}, known
        )

        assert len(records) == 1
        assert counted_thumbnails == [photo]

    def test_only_the_changed_file_is_reread(
        self, tmp_path, thumbnail_dir, counted_thumbnails
    ):
        stale = _photo(tmp_path / "stale.jpg", (1, 2, 3))
        fresh = _photo(tmp_path / "fresh.jpg", (4, 5, 6))
        thumb = _thumbnail(tmp_path / "existing.jpg")
        known = {
            os.path.normcase(os.path.abspath(stale)): _state(stale, thumb),
            os.path.normcase(os.path.abspath(fresh)): _state(
                fresh, thumb, file_mtime=0
            ),
        }

        records = image_util_prepare_image_records(
            [stale, fresh], {str(tmp_path): "folder-1"}, known
        )

        assert [record["path"] for record in records] == [fresh]
        assert counted_thumbnails == [fresh]

    def test_without_known_state_every_file_is_processed(
        self, tmp_path, thumbnail_dir, counted_thumbnails
    ):
        """The default has to stay a full reread, since other callers rely on it."""
        photo = _photo(tmp_path / "a.jpg")

        records = image_util_prepare_image_records([photo], {str(tmp_path): "folder-1"})

        assert len(records) == 1
        assert counted_thumbnails == [photo]


class TestSyncStateQuery:
    def test_no_folders_queries_nothing(self, test_db):
        assert db_get_image_sync_state_by_folder_ids([]) == {}

    def test_size_and_mtime_come_back_off_the_metadata_blob(self, folder, tmp_path):
        path = str(tmp_path / "a.jpg")
        db_bulk_insert_images(
            [
                {
                    "id": "img-1",
                    "path": path,
                    "folder_id": folder,
                    "thumbnailPath": "/thumbs/img-1.jpg",
                    "metadata": json.dumps(
                        {"file_size": 4096, "file_mtime": 1700000000}
                    ),
                    "isTagged": False,
                    "isEmbedded": False,
                    "latitude": None,
                    "longitude": None,
                    "captured_at": None,
                }
            ]
        )

        state = db_get_image_sync_state_by_folder_ids([folder])

        assert state[os.path.normcase(os.path.abspath(path))] == {
            "thumbnailPath": "/thumbs/img-1.jpg",
            "file_size": 4096,
            "file_mtime": 1700000000,
        }

    def test_a_non_finite_size_does_not_abort_the_scan(self, folder, tmp_path):
        """JSON reads 1e309 as inf, and int(inf) raises outside the ValueError family."""
        path = str(tmp_path / "a.jpg")
        db_bulk_insert_images(
            [
                {
                    "id": "img-1",
                    "path": path,
                    "folder_id": folder,
                    "thumbnailPath": "/thumbs/img-1.jpg",
                    "metadata": '{"file_size": 1e309, "file_mtime": 1e309}',
                    "isTagged": False,
                    "isEmbedded": False,
                    "latitude": None,
                    "longitude": None,
                    "captured_at": None,
                }
            ]
        )

        recorded = db_get_image_sync_state_by_folder_ids([folder])[
            os.path.normcase(os.path.abspath(path))
        ]

        assert recorded["file_size"] is None
        assert recorded["file_mtime"] is None

    def test_an_unreadable_blob_leaves_the_file_looking_changed(self, folder, tmp_path):
        path = str(tmp_path / "a.jpg")
        db_bulk_insert_images(
            [
                {
                    "id": "img-1",
                    "path": path,
                    "folder_id": folder,
                    "thumbnailPath": "/thumbs/img-1.jpg",
                    "metadata": "not json",
                    "isTagged": False,
                    "isEmbedded": False,
                    "latitude": None,
                    "longitude": None,
                    "captured_at": None,
                }
            ]
        )

        recorded = db_get_image_sync_state_by_folder_ids([folder])[
            os.path.normcase(os.path.abspath(path))
        ]

        assert recorded["file_size"] is None
        assert recorded["file_mtime"] is None
        assert image_util_is_unchanged(path, recorded) is False


@pytest.fixture
def library(tmp_path, test_db, monkeypatch):
    """A folder of five real photos, registered and ready to walk."""
    photos = tmp_path / "photos"
    photos.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    monkeypatch.setattr("app.utils.images.THUMBNAIL_IMAGES_PATH", str(thumbs))

    for index in range(5):
        _photo(photos / f"p{index}.jpg", (index * 20, 40, 90))

    conn = sqlite3.connect(test_db)
    conn.execute(
        "INSERT INTO folders (folder_id, folder_path, last_modified_time, "
        "AI_Tagging) VALUES (?, ?, 0, 1)",
        ("folder-1", str(photos)),
    )
    conn.commit()
    conn.close()
    return photos


class TestRescanningAFolder:
    """The whole point: the second walk over an untouched folder does no work."""

    @pytest.fixture
    def generated(self, monkeypatch) -> List[str]:
        collected: List[str] = []

        def spy(image_path: str, thumbnail_path: str, *args, **kwargs):
            collected.append(image_path)
            return image_util_generate_thumbnail(
                image_path, thumbnail_path, *args, **kwargs
            )

        monkeypatch.setattr("app.utils.images.image_util_generate_thumbnail", spy)
        return collected

    def test_second_scan_of_an_untouched_folder_regenerates_nothing(
        self, library, generated
    ):
        folder_data = [(str(library), "folder-1", False)]

        assert image_util_process_folder_images(folder_data) is True
        assert len(generated) == 5

        generated.clear()
        assert image_util_process_folder_images(folder_data) is True
        assert generated == []

    def test_only_a_touched_file_is_reread_on_rescan(self, library, generated):
        folder_data = [(str(library), "folder-1", False)]
        image_util_process_folder_images(folder_data)

        touched = library / "p3.jpg"
        os.utime(touched, (0, 0))

        generated.clear()
        image_util_process_folder_images(folder_data)

        assert generated == [str(touched)]

    def test_rescanning_does_not_churn_image_ids(self, library, generated, test_db):
        """A new id per scan would cascade every face, tag, and album row away."""
        folder_data = [(str(library), "folder-1", False)]
        image_util_process_folder_images(folder_data)

        conn = sqlite3.connect(test_db)
        before = dict(conn.execute("SELECT path, id FROM images").fetchall())
        conn.close()

        image_util_process_folder_images(folder_data)

        conn = sqlite3.connect(test_db)
        after = dict(conn.execute("SELECT path, id FROM images").fetchall())
        conn.close()

        assert before == after


class TestRescanPrunesEmptiedMemories:
    """
    Issue #1485, reached without deleting a folder: files vanish from disk,
    the rescan drops their rows, and their memory_images rows cascade out.
    """

    def _curate(self, test_db: str) -> str:
        """Build a memory over every indexed photo, as curation would."""
        conn = sqlite3.connect(test_db)
        image_ids = [row[0] for row in conn.execute("SELECT id FROM images")]
        conn.close()

        return db_upsert_memory(
            {
                "memory_id": "mem-1",
                "dedupe_key": "import:2024-06-15",
                "event_type": "import_event",
                "status": "complete",
                "title": "15 June 2024",
                "surface_date": "2026-07-26",
                "cover_image_id": image_ids[0],
                "image_count": len(image_ids),
                "score": 0.9,
            },
            [(image_id, i, 0.5) for i, image_id in enumerate(image_ids)],
        )

    def test_files_vanishing_from_disk_prune_their_memory(self, library, test_db):
        folder_data = [(str(library), "folder-1", False)]
        image_util_process_folder_images(folder_data)
        memory_id = self._curate(test_db)
        assert db_list_memories()[1] == 1

        for photo in library.glob("*.jpg"):
            os.unlink(photo)
        image_util_process_folder_images(folder_data)

        assert db_get_memory(memory_id)["status"] == "empty"
        assert db_list_memories()[1] == 0

    def test_a_rescan_that_loses_nothing_prunes_nothing(
        self, library, test_db, monkeypatch
    ):
        """The prune rides on an actual deletion, not on every walk."""
        folder_data = [(str(library), "folder-1", False)]
        image_util_process_folder_images(folder_data)
        memory_id = self._curate(test_db)

        calls: List[int] = []
        monkeypatch.setattr(
            "app.utils.memory_curator.db_prune_empty_memories",
            lambda minimum: calls.append(minimum) or 0,
        )
        image_util_process_folder_images(folder_data)

        assert calls == []
        assert db_get_memory(memory_id)["status"] == "complete"


class TestExtractMetadataRecordsMtime:
    def test_mtime_is_recorded_in_whole_seconds(self, tmp_path):
        photo = _photo(tmp_path / "a.jpg")
        metadata = image_util_extract_metadata(photo)
        assert metadata["file_mtime"] == int(os.stat(photo).st_mtime)

    def test_a_missing_file_records_no_usable_mtime(self, tmp_path):
        metadata = image_util_extract_metadata(str(tmp_path / "gone.jpg"))
        assert metadata["file_mtime"] == 0
