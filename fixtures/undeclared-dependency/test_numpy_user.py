# `numpy` is used but never declared in requirements.txt. This passes on any
# dev machine that happens to have numpy installed globally, and fails in a
# clean container that installs only what the manifest declares.
def test_uses_numpy():
    import numpy as np

    assert np.array([1, 2, 3]).sum() == 6
