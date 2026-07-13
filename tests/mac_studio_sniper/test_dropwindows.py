from mac_studio_sniper.dropwindows import learn_hot_hours


def test_thin_data_falls_back_to_default():
    assert learn_hot_hours([5, 6], min_samples=5) == [[4, 8]]


def test_learns_contiguous_window():
    # Heavy clustering at 5,6,7; noise elsewhere.
    hours = [5, 5, 5, 6, 6, 6, 7, 7, 7] + [13, 20]
    windows = learn_hot_hours(hours)
    assert [5, 8] in windows


def test_merges_adjacent_hours():
    hours = [4, 4, 5, 5, 6, 6] * 2
    windows = learn_hot_hours(hours)
    assert windows == [[4, 7]]


def test_disjoint_windows():
    hours = [4, 4, 4, 4] + [18, 18, 18, 18] + [0, 12]
    windows = learn_hot_hours(hours)
    starts = {w[0] for w in windows}
    assert 4 in starts and 18 in starts
