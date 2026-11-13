from nyse_pillar_fix.store import FileStore, MemoryStore
from nyse_pillar_fix.throttle import RollingWindowThrottle


def test_memory_store_range():
    s = MemoryStore()
    for i in range(1, 6):
        s.store_sent(i, f"m{i}".encode())
    assert [x for x, _ in s.get_sent(2, 4)] == [2, 3, 4]
    assert [x for x, _ in s.get_sent(4, 0)] == [4, 5]


def test_file_store_persists(tmp_path):
    s = FileStore(tmp_path)
    s.set_next_sender_seq(7)
    s.set_next_target_seq(3)
    s.store_sent(6, b"8=FIX.4.2\x019=5\x0135=D\x0110=000\x01")
    s.close()
    t = FileStore(tmp_path)
    assert (t.next_sender_seq, t.next_target_seq) == (7, 3)
    assert t.get_sent(1, 0) == [(6, b"8=FIX.4.2\x019=5\x0135=D\x0110=000\x01")]
    t.reset()
    assert (t.next_sender_seq, t.next_target_seq, t.get_sent(1, 0)) == (1, 1, [])
    t.close()


def test_throttle_rolling_window():
    th = RollingWindowThrottle(500, 0.1)
    assert all(th.try_acquire(now=0.0) for _ in range(500))
    assert not th.try_acquire(now=0.05)
    assert th.delay(now=0.05) == 0.05
    assert th.try_acquire(now=0.1)
