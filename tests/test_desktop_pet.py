from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_desktop_pet_entry_and_page_are_wired():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'id="desktopPetCard"' in html
    assert 'id="desktopPetPage"' in html
    assert "我的桌宠" in html
    assert "学习搭子 · 黑松克" not in html
    assert 'data-pet-action="wave"' in html
    assert 'data-pet-action="crosslink"' in html
    assert '$("desktopPetCard")?.addEventListener("click", openDesktopPet)' in script


def test_desktop_pet_animation_frames_are_complete():
    expected = {
        "normal": 9,
        "wave": 12,
        "idea": 12,
        "followup": 16,
        "crosslink": 16,
        "levelup": 16,
    }

    for action, count in expected.items():
        folder = WEB / "assets" / "pet" / action
        frames = sorted(folder.glob(f"{action}_*.webp"))
        assert len(frames) == count, f"{action} 应有 {count} 帧，实际为 {len(frames)} 帧"
        assert all(frame.stat().st_size > 0 for frame in frames)
