"""最近一次可撤销操作的内存栈。

放在内存的理由：用户要的语义是「刚点错了马上退回」，不是审计日志。
- 只保留最近一条，新的操作把它顶掉（与前端提示条「撤销 / 关闭」的交互一致）；
- 进程重启即失效，这是刻意接受的成本，换来零迁移、零新表；
- 快照就是被删行的原样 JSON，恢复时按原样插回，display_id / executed_at 都不会变。

不可撤销的操作（物理删除文件）根本不入栈——文件已经不在磁盘上了，任何「恢复」
都只是撒谎。前端据此不显示撤销按钮。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UndoSnapshot:
    """一次可撤销操作的完整现场。"""

    kind: str  # record_delete | record_clear
    message: str  # 给用户看的一句话（例如「已删除 3 条记录」）
    records: list[dict] = field(default_factory=list)  # history_records 整行快照
    files: list[dict] = field(default_factory=list)  # 关联 scraped_files 整行快照
    jobs: list[dict] = field(default_factory=list)  # 清空时删除的任务及 locator/高级配置


class UndoStore:
    """只保留最近一条快照的栈。"""

    def __init__(self) -> None:
        self._snapshot: UndoSnapshot | None = None

    def push(self, snapshot: UndoSnapshot) -> None:
        self._snapshot = snapshot

    def peek(self) -> UndoSnapshot | None:
        return self._snapshot

    def pop(self) -> UndoSnapshot | None:
        snapshot = self._snapshot
        self._snapshot = None
        return snapshot

    def clear(self) -> None:
        self._snapshot = None
