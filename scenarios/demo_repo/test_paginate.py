import pytest

from paginate import paginate


def test_every_item_appears_once_in_order():
    pages = paginate(list(range(10)), 3)
    assert [x for page in pages for x in page] == list(range(10))


def test_page_sizes():
    assert [len(p) for p in paginate(list(range(10)), 3)] == [3, 3, 3, 1]


def test_rejects_bad_page_size():
    with pytest.raises(ValueError):
        paginate([1, 2, 3], 0)
