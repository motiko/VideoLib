import pytest
from pathlib import Path
from src.config import config
from src.storage.manager import StorageManager

@pytest.fixture
def temp_storage(tmp_path):
    """Fixture that instantiates a StorageManager in a pytest temp directory."""
    manager = StorageManager(tmp_path)
    manager.init_dir()
    return manager

def test_generate_path(temp_storage):
    """Verify generated paths are unique, reside in target dir, and end with the correct suffix."""
    path1 = temp_storage.generate_path(".mp4")
    path2 = temp_storage.generate_path("mp4")
    
    assert path1 != path2
    assert path1.parent == temp_storage.download_dir
    assert path1.suffix == ".mp4"
    assert path2.suffix == ".mp4"

@pytest.mark.asyncio
async def test_cleanup_valid_file(temp_storage):
    """Verify cleanup successfully removes an existing file."""
    config.KEEP_TMP_FILES = False
    path = temp_storage.generate_path(".mp4")
    path.write_text("dummy content")
    assert path.exists()
    
    deleted = await temp_storage.cleanup(path)
    assert deleted is True
    assert not path.exists()

@pytest.mark.asyncio
async def test_cleanup_keep_tmp_files(temp_storage):
    """Verify cleanup preserves files when KEEP_TMP_FILES is set to True."""
    config.KEEP_TMP_FILES = True
    try:
        path = temp_storage.generate_path(".mp4")
        path.write_text("dummy content to keep")
        assert path.exists()
        
        deleted = await temp_storage.cleanup(path)
        assert deleted is False
        assert path.exists()  # File MUST remain intact!
    finally:
        config.KEEP_TMP_FILES = False
        if path.exists():
            path.unlink()

@pytest.mark.asyncio
async def test_cleanup_nonexistent_file(temp_storage):
    """Verify cleaning up a file that doesn't exist returns False cleanly."""
    config.KEEP_TMP_FILES = False
    path = temp_storage.generate_path(".mp4")
    assert not path.exists()
    
    deleted = await temp_storage.cleanup(path)
    assert deleted is False

@pytest.mark.asyncio
async def test_cleanup_traversal_protection(temp_storage, tmp_path):
    """Verify storage manager prevents deletion of files outside its download directory."""
    config.KEEP_TMP_FILES = False
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("sensitive data")
    
    assert outside_file.exists()
    
    deleted = await temp_storage.cleanup(outside_file)
    
    assert deleted is False
    assert outside_file.exists()
    
    outside_file.unlink()
    outside_dir.rmdir()
