"""Sequence-number and sent-message persistence.

Pillar never resets sequence numbers intraday through the session (ResetSeqNumFlag
must be N); numbers start at 1 each trading day, or after a DR failover or a session
restart with a software rollback (section 1.3). Call :meth:`MessageStore.reset` in
those cases.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class MessageStore(ABC):
    @property
    @abstractmethod
    def next_sender_seq(self) -> int: ...

    @property
    @abstractmethod
    def next_target_seq(self) -> int: ...

    @abstractmethod
    def set_next_sender_seq(self, seq: int) -> None: ...

    @abstractmethod
    def set_next_target_seq(self, seq: int) -> None: ...

    @abstractmethod
    def store_sent(self, seq: int, raw: bytes) -> None: ...

    @abstractmethod
    def get_sent(self, begin: int, end: int) -> list[tuple[int, bytes]]:
        """Sent messages with ``begin <= seq <= end`` (``end`` 0 means no upper bound)."""

    @abstractmethod
    def reset(self) -> None: ...


class MemoryStore(MessageStore):
    def __init__(self, next_sender_seq: int = 1, next_target_seq: int = 1) -> None:
        self._sender = next_sender_seq
        self._target = next_target_seq
        self._sent: dict[int, bytes] = {}

    @property
    def next_sender_seq(self) -> int:
        return self._sender

    @property
    def next_target_seq(self) -> int:
        return self._target

    def set_next_sender_seq(self, seq: int) -> None:
        self._sender = seq

    def set_next_target_seq(self, seq: int) -> None:
        self._target = seq

    def store_sent(self, seq: int, raw: bytes) -> None:
        self._sent[seq] = raw

    def get_sent(self, begin: int, end: int) -> list[tuple[int, bytes]]:
        return sorted((s, r) for s, r in self._sent.items() if s >= begin and (end == 0 or s <= end))

    def reset(self) -> None:
        self._sender = self._target = 1
        self._sent.clear()


class FileStore(MemoryStore):
    """Durable store: ``seqnums.json`` plus an append-only ``messages.log`` in *directory*."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        super().__init__()
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._seq_path = self._dir / "seqnums.json"
        self._log_path = self._dir / "messages.log"
        if self._seq_path.exists():
            data = json.loads(self._seq_path.read_text())
            self._sender = int(data["next_sender_seq"])
            self._target = int(data["next_target_seq"])
        if self._log_path.exists():
            with self._log_path.open("rb") as fh:
                for line in fh:
                    seq, _, raw = line.rstrip(b"\n").partition(b"\t")
                    if seq.isdigit():
                        self._sent[int(seq)] = raw
        self._log = self._log_path.open("ab")

    def _flush_seqs(self) -> None:
        tmp = self._seq_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"next_sender_seq": self._sender, "next_target_seq": self._target}))
        os.replace(tmp, self._seq_path)

    def set_next_sender_seq(self, seq: int) -> None:
        super().set_next_sender_seq(seq)
        self._flush_seqs()

    def set_next_target_seq(self, seq: int) -> None:
        super().set_next_target_seq(seq)
        self._flush_seqs()

    def store_sent(self, seq: int, raw: bytes) -> None:
        super().store_sent(seq, raw)
        self._log.write(str(seq).encode() + b"\t" + raw + b"\n")
        self._log.flush()

    def reset(self) -> None:
        super().reset()
        self._log.close()
        self._log_path.unlink(missing_ok=True)
        self._log = self._log_path.open("ab")
        self._flush_seqs()

    def close(self) -> None:
        self._log.close()
