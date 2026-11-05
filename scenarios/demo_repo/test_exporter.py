from exporter import to_csv


def test_basic_rows():
    assert to_csv([{"a": 1, "b": 2}, {"a": 3, "b": 4}]) == "a,b\r\n1,2\r\n3,4\r\n"


def test_empty_result_set():
    assert to_csv([]) == ""
