import os


# A 0444 file's owner-write bit is off. As a non-root user this correctly
# raises PermissionError. Root bypasses Unix permission bits entirely, so
# the SAME assertion silently passes for the wrong reason when run as root
# -- which is exactly why cleanroom runs as non-root by default.
def test_readonly_write_denied(tmp_path):
    ro = tmp_path / "locked.txt"
    ro.write_text("original")
    os.chmod(ro, 0o444)

    raised = False
    try:
        with open(ro, "w") as f:
            f.write("overwritten")
    except PermissionError:
        raised = True

    assert raised, "expected PermissionError when writing a 0444 file"
