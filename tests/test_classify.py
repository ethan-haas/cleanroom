from cleanroom.classify import classify


def test_classifies_undeclared_dependency():
    out = "ModuleNotFoundError: No module named 'numpy'"
    assert classify("test", out) == "undeclared-dependency"


def test_classifies_undeclared_dependency_node():
    out = "Error: Cannot find module 'left-pad'"
    assert classify("test", out) == "undeclared-dependency"


def test_classifies_path_depth():
    out = (
        "  File \"/r/conftest.py\", line 3, in <module>\n"
        "    ROOT = Path(__file__).resolve().parents[2]\n"
        "           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^\n"
        "IndexError: tuple index out of range\n"
    )
    assert classify("test", out) == "path-depth-assumption"


def test_classifies_missing_service():
    out = "ConnectionRefusedError: [Errno 111] Connection refused"
    assert classify("test", out) == "missing-service"


def test_classifies_genuine_platform_mismatch():
    out = (
        "npm error code EBADPLATFORM\n"
        "npm error notsup Unsupported platform for darwin-only-fixture-dep@1.0.0: "
        'wanted {"os":"darwin","cpu":"arm64"} (current: {"os":"linux","cpu":"x64"})\n'
        "npm error notsup Valid os:   darwin\n"
        "npm error notsup Actual os:  linux\n"
    )
    assert classify("install", out) == "lockfile-platform-mismatch"


def test_does_not_classify_typod_dependency_as_platform_mismatch():
    # E1: a nonexistent/typo'd package -> npm E404. Zero platform dimension.
    out = (
        "npm error code E404\n"
        "npm error 404 Not Found - GET https://registry.npmjs.org/left-pad-fixture-dep - Not found\n"
        "npm error 404  'left-pad-fixture-dep@^1.0.0' is not in this registry.\n"
    )
    assert classify("install", out) == "unclassified"


def test_does_not_classify_out_of_sync_lockfile_as_platform_mismatch():
    # E2: package.json edited without regenerating the lockfile -> EUSAGE.
    out = (
        "npm error code EUSAGE\n"
        "npm error `npm ci` can only install packages when your package.json and "
        "package-lock.json or npm-shrinkwrap.json are in sync. Please update your "
        "lock file with `npm install`"
    )
    assert classify("install", out) == "unclassified"


def test_does_not_classify_missing_lockfile_as_platform_mismatch():
    # E3: package-lock.json missing entirely -> also EUSAGE, also no platform dimension.
    out = (
        "npm error code EUSAGE\n"
        "npm error The `npm ci` command can only install with an existing "
        "package-lock.json or npm-shrinkwrap.json with lockfileVersion >= 1."
    )
    assert classify("install", out) == "unclassified"


def test_classifies_untracked_file():
    out = "FileNotFoundError: [Errno 2] No such file or directory: '/r/secret.local'"
    assert classify("test", out, gitignore_patterns=["secret.local"]) == "untracked-file-dependency"


def test_does_not_classify_untracked_file_without_gitignore_match():
    out = "FileNotFoundError: [Errno 2] No such file or directory: '/r/unrelated.txt'"
    assert classify("test", out, gitignore_patterns=["secret.local"]) == "unclassified"


def test_classifies_root_only_only_when_run_as_root():
    out = "AssertionError: expected PermissionError when writing a 0444 file"
    assert classify("test", out, run_as_root=False) == "unclassified"
    assert classify("test", out, run_as_root=True) == "root-only-permission-behaviour"


def test_unclassified_when_nothing_matches():
    assert classify("test", "some unrelated failure text with no known signature") == "unclassified"
