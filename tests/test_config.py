from app.config import SettingsStore, migrate_legacy_dir


def test_legacy_data_folder_is_moved_once(tmp_path):
    old, new = tmp_path / "CroweVibeWhisper", tmp_path / "LexiVox"
    (old / "models").mkdir(parents=True)
    (old / "config.json").write_text('{"hotkey": "Ctrl+Win"}')
    assert migrate_legacy_dir(old, new) == new
    assert not old.exists() and (new / "models").is_dir()
    assert SettingsStore(new / "config.json").get().hotkey == "Ctrl+Win"
    # Later runs leave things alone, even if an old folder reappears.
    old.mkdir()
    assert migrate_legacy_dir(old, new) == new and old.exists()


def test_fresh_install_uses_new_folder(tmp_path):
    assert migrate_legacy_dir(tmp_path / "old", tmp_path / "new") == tmp_path / "new"


def test_settings_file_with_bom_is_read(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"theme": "dark"}', encoding="utf-8-sig")
    assert SettingsStore(path).get().theme == "dark"
