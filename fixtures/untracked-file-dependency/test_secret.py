from pathlib import Path

# secret.local exists in the developer's working tree but is gitignored --
# never committed. Passes here (the worktree), fails from a tracked-only
# clone. The contrast between the two is the whole point of this fixture.
def test_secret_file_present():
    p = Path(__file__).resolve().parent / "secret.local"
    assert p.read_text() == "topsecret\n"
