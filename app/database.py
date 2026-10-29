# -*- coding: utf-8 -*-
"""
SQLite 数据访问层。

表结构：
    students       人员（学生/员工）档案，EPC ↔ 编号 ↔ 姓名
    attendance     考勤事件流（每次刷卡 = 一条）
    settings       键值对（学校名、签到/签退时间阈值等）
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional, Tuple

from . import app_data_dir


DB_PATH = os.path.join(app_data_dir(), "attendance.db")


class EpcConflictError(RuntimeError):
    """这张卡（EPC）已经绑给别的学号了。

    这是"新卡一刷就变成别人"这类事故的根源：
    以前 upsert_student 遇到 EPC 已被占用时会**静默改绑**，
    旧卡的主人被顶掉、新卡顶着旧名字显示，看起来就像"认错人"。
    现在改成显式抛异常，由界面弹窗让操作者确认。
    """

    def __init__(self, epc: str, owner_sid: str, owner_name: str) -> None:
        super().__init__(f"EPC {epc} 已绑定给 {owner_sid} {owner_name}")
        self.epc = epc
        self.owner_sid = owner_sid
        self.owner_name = owner_name

    @property
    def owner_display(self) -> str:
        return f"{self.owner_sid} {self.owner_name}".strip()


SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    epc         TEXT    UNIQUE,
    sid         TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    note        TEXT    DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now','localtime')),
    updated_at  TEXT    DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_students_epc  ON students(epc);
CREATE INDEX IF NOT EXISTS idx_students_sid  ON students(sid);

CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER REFERENCES students(id) ON DELETE SET NULL,
    epc         TEXT    NOT NULL,
    sid         TEXT,
    name        TEXT,
    event_type  TEXT    NOT NULL CHECK(event_type IN ('in','out','unknown')),
    ts          TEXT    NOT NULL,
    note        TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_att_ts    ON attendance(ts);
CREATE INDEX IF NOT EXISTS idx_att_epc   ON attendance(epc);
CREATE INDEX IF NOT EXISTS idx_att_sid   ON attendance(sid);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class Student:
    id: int
    epc: Optional[str]
    sid: str
    name: str
    note: str
    updated_at: str = ""
    created_at: str = ""      # 登记时间，卡有效期判定要用

    @property
    def display(self) -> str:
        if self.sid and self.name:
            return f"{self.sid}  {self.name}"
        return self.name or self.sid or "未命名"


@dataclass
class AttendanceEvent:
    id: int
    sid: str
    name: str
    epc: str
    event_type: str   # 'in' / 'out' / 'unknown'
    ts: datetime

    @property
    def type_label(self) -> str:
        return {"in": "签到", "out": "签退", "unknown": "未知卡"}.get(self.event_type, "未知")


class Database:
    """SQLite 封装。线程安全（每个调用使用新连接）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        # 注意：不要写成 `path: str = DB_PATH`。
        # 默认值会在 import 时被冻结，运行期再改 DB_PATH 就不生效了
        # （曾经导致测试写进了真实库）。
        self.path = path or DB_PATH
        self._init_schema()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        c.execute("PRAGMA journal_mode = WAL;")
        try:
            yield c
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ---------- 人员 ----------

    def list_students(self) -> List[Student]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, epc, sid, name, note, created_at, updated_at FROM students "
                "ORDER BY sid, name"
            ).fetchall()
        return [Student(r["id"], r["epc"], r["sid"], r["name"], r["note"] or "",
                        (r["updated_at"] or "")[:19],
                        (r["created_at"] or "")[:19]) for r in rows]

    def generate_unique_epc(self, prefix: str = "E2", length: int = 24) -> str:
        """生成一个库中尚不存在的唯一 EPC（默认 24 位十六进制 = 12 字节）。

        为什么需要它：空白 UHF 卡出厂 EPC 往往**完全相同**
        （同批次同默认值），于是多张卡读出来一模一样，
        刷到谁都显示成同一个"已注册的人"。
        发卡时给每张卡烧一个独一无二的卡号，才能一卡一人。
        """
        prefix = (prefix or "E2").upper()[:4]
        length = max(8, int(length))
        for _ in range(500):
            need = (length - len(prefix) + 1) // 2
            epc = (prefix + os.urandom(need).hex().upper())[:length]
            if not self.get_student_by_epc(epc):
                return epc
        # 极端兜底：时间戳派生
        epc = (prefix + f"{int(time.time()):016X}")[:length]
        while self.get_student_by_epc(epc):
            epc = (prefix + os.urandom(8).hex().upper())[:length]
        return epc

    def get_student_by_epc(self, epc: str) -> Optional[Student]:
        epc = (epc or "").strip().upper()
        if not epc:
            return None
        with self._conn() as c:
            r = c.execute(
                "SELECT id, epc, sid, name, note, created_at FROM students "
                "WHERE epc = ?", (epc,)
            ).fetchone()
        if not r:
            return None
        return Student(r["id"], r["epc"], r["sid"], r["name"], r["note"] or "",
                       "", (r["created_at"] or "")[:19])

    def get_student_by_id(self, sid: int) -> Optional[Student]:
        with self._conn() as c:
            r = c.execute(
                "SELECT id, epc, sid, name, note, created_at FROM students "
                "WHERE id = ?", (sid,)
            ).fetchone()
        if not r:
            return None
        return Student(r["id"], r["epc"], r["sid"], r["name"], r["note"] or "",
                       "", (r["created_at"] or "")[:19])

    def get_student_by_sid(self, sid: str) -> Optional[Student]:
        """按学号/工号查询（取最早的一条）。"""
        with self._conn() as c:
            r = c.execute(
                "SELECT id, epc, sid, name, note, created_at FROM students "
                "WHERE sid = ? ORDER BY id LIMIT 1", (sid,)
            ).fetchone()
        if not r:
            return None
        return Student(r["id"], r["epc"], r["sid"], r["name"], r["note"] or "",
                       "", (r["created_at"] or "")[:19])

    def list_recent_students(self, limit: int = 12) -> List[dict]:
        """最近注册/更新的学生，用于「学生注册」页底部回显。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, sid, name, epc, created_at, updated_at FROM students "
                "ORDER BY datetime(updated_at) DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_student(self, sid: str, name: str, epc: Optional[str] = None,
                       note: str = "", student_id: Optional[int] = None,
                       allow_rebind: bool = False) -> int:
        """新增或更新人员。返回记录 id。

        allow_rebind=False（默认）时，**绝不覆盖别人已绑定的卡**：
        一旦这张 EPC 已经绑给其他人，抛 EpcConflictError，
        由界面弹窗让操作者明确确认后再传 allow_rebind=True。
        """
        epc_u = epc.upper() if epc else None
        with self._conn() as c:
            if student_id is not None:
                # 改绑：先确认这张卡现在是不是别人的
                if epc_u:
                    other = c.execute(
                        "SELECT id, sid, name FROM students WHERE epc=? AND id<>?",
                        (epc_u, student_id),
                    ).fetchone()
                    if other and not allow_rebind:
                        raise EpcConflictError(epc_u, other["sid"], other["name"])
                c.execute(
                    "UPDATE students SET sid=?, name=?, epc=?, note=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (sid, name, epc_u, note, student_id),
                )
                return student_id
            # INSERT OR IGNORE：如果 EPC 已存在则更新
            if epc_u:
                existing = c.execute(
                    "SELECT id, sid, name FROM students WHERE epc=?", (epc_u,)
                ).fetchone()
                if existing:
                    if not allow_rebind:
                        raise EpcConflictError(epc_u, existing["sid"], existing["name"])
                    c.execute(
                        "UPDATE students SET sid=?, name=?, note=?, "
                        "updated_at=datetime('now','localtime') WHERE id=?",
                        (sid, name, note, existing["id"]),
                    )
                    return existing["id"]
            cur = c.execute(
                "INSERT INTO students (sid, name, epc, note) VALUES (?,?,?,?)",
                (sid, name, epc_u, note),
            )
            return cur.lastrowid

    def delete_student(self, student_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM students WHERE id=?", (student_id,))

    # ---------- 考勤事件 ----------

    def add_event(self, epc: str, student: Optional[Student], event_type: str,
                  ts: Optional[datetime] = None) -> int:
        ts = ts or datetime.now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO attendance (student_id, epc, sid, name, event_type, ts) "
                "VALUES (?,?,?,?,?,?)",
                (student.id if student else None, epc.upper(),
                 student.sid if student else "", student.name if student else "",
                 event_type, ts.strftime("%Y-%m-%d %H:%M:%S")),
            )
            return cur.lastrowid

    def get_last_event(self, epc: str) -> Optional[AttendanceEvent]:
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM attendance WHERE epc=? ORDER BY id DESC LIMIT 1",
                (epc.upper(),),
            ).fetchone()
        if not r:
            return None
        return self._row_to_event(r)

    def get_last_event_today(self, epc: str) -> Optional[AttendanceEvent]:
        """当天该卡最后一条记录。

        判定签到/签退必须只看当天：跨天后第一张卡要重新算「签到」，
        否则会继承上一次的签到状态，把新一天的第一刷误判成签退。
        """
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM attendance WHERE epc=? "
                "AND date(ts)=date('now','localtime') ORDER BY id DESC LIMIT 1",
                (epc.upper(),),
            ).fetchone()
        if not r:
            return None
        return self._row_to_event(r)

    def query_events(self, d_from: Optional[date] = None, d_to: Optional[date] = None,
                     sid: Optional[str] = None, limit: int = 500) -> List[AttendanceEvent]:
        clauses, params = [], []
        if d_from:
            clauses.append("date(ts) >= date(?)"); params.append(d_from.isoformat())
        if d_to:
            clauses.append("date(ts) <= date(?)"); params.append(d_to.isoformat())
        if sid:
            clauses.append("sid = ?"); params.append(sid)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM attendance {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query_events_by_epc(self, epc: str, limit: int = 500) -> List[AttendanceEvent]:
        """某张卡的全部刷卡记录（最新的在前）。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM attendance WHERE epc=? ORDER BY id DESC LIMIT ?",
                (epc.upper(), limit),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count_events_by_epc(self, epc: str) -> dict:
        """某张卡的累计统计：总次数 / 签到 / 签退 / 未知，以及首末次时间。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_type, COUNT(*) n, MIN(ts) first_ts, MAX(ts) last_ts "
                "FROM attendance WHERE epc=? GROUP BY event_type", (epc.upper(),)
            ).fetchall()
        stat = {"total": 0, "in": 0, "out": 0, "unknown": 0,
                "first_ts": "", "last_ts": ""}
        firsts, lasts = [], []
        for r in rows:
            n = r["n"] or 0
            stat["total"] += n
            stat[r["event_type"]] = stat.get(r["event_type"], 0) + n
            if r["first_ts"]:
                firsts.append(r["first_ts"])
            if r["last_ts"]:
                lasts.append(r["last_ts"])
        stat["first_ts"] = min(firsts) if firsts else ""
        stat["last_ts"] = max(lasts) if lasts else ""
        return stat

    def get_today_events(self) -> List[AttendanceEvent]:
        return self.query_events(d_from=date.today(), d_to=date.today(), limit=1000)

    def clear_today_events(self) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM attendance WHERE date(ts) = date('now','localtime')")
            return cur.rowcount

    def count_today_checkins(self) -> int:
        with self._conn() as c:
            r = c.execute(
                "SELECT COUNT(DISTINCT sid) AS n FROM attendance "
                "WHERE event_type='in' AND date(ts)=date('now','localtime')"
            ).fetchone()
        return r["n"] or 0

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> AttendanceEvent:
        try:
            ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            ts = datetime.now()
        return AttendanceEvent(
            id=r["id"], sid=r["sid"] or "", name=r["name"] or "",
            epc=r["epc"] or "", event_type=r["event_type"] or "unknown", ts=ts,
        )

    # ---------- 设置 ----------

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_all_settings(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT key,value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---------- 卡有效期 ----------

    def get_validity_range(self) -> Tuple[Optional[date], Optional[date]]:
        """卡的有效期区间（起始日, 截止日）。留空的那一侧返回 None = 不限制。"""
        def _parse(s: str) -> Optional[date]:
            try:
                return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
            except ValueError:
                return None
        return (_parse(self.get_setting("valid_from", "")),
                _parse(self.get_setting("valid_until", "")))

    def judge_card_validity(self, student: Optional[Student],
                            now: Optional[datetime] = None) -> Tuple[bool, str]:
        """判断一张卡现在能不能用。返回 (能否使用, 不可用时的原因)。

        两条规则同时生效（区间两侧留空表示不限制）：
          1. 卡的登记日期必须落在区间内 —— 不在这段时间里登记的卡不算数；
          2. 今天也必须落在区间内 —— 一旦过了截止日，这批卡整批失效。
        """
        f, t = self.get_validity_range()
        if f is None and t is None:
            return True, ""
        today = (now or datetime.now()).date()
        span = "有效期 %s ~ %s" % (
            f.isoformat() if f else "不限", t.isoformat() if t else "不限")

        # 规则 2：当前日期是否还在区间内
        if f is not None and today < f:
            return False, "尚未启用（%s 起生效）" % f.isoformat()
        if t is not None and today > t:
            return False, "已超过有效期（%s）" % span

        # 规则 1：该卡的登记日期是否落在区间内
        reg = self._register_date(student)
        if reg is not None:
            if f is not None and reg < f:
                return False, "该卡登记于 %s，早于启用日期（%s）" % (
                    reg.isoformat(), span)
            if t is not None and reg > t:
                return False, "该卡登记于 %s，晚于截止日期（%s）" % (
                    reg.isoformat(), span)
        return True, ""

    @staticmethod
    def _register_date(student: Optional[Student]) -> Optional[date]:
        """取这张卡的登记日期；取不到返回 None（此时跳过「登记时间」这条校验）。"""
        if student is None:
            return None
        s = (student.created_at or "").strip()
        if not s:
            return None
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    # ---------- 一次性：从 students.txt 导入旧数据 ----------

    def import_students_txt(self, path: str) -> int:
        """从你原来的 students.txt 导入已有人员。返回新增/更新条数。"""
        if not os.path.exists(path):
            return 0
        cnt = 0
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    self.upsert_student(sid=parts[1], name=parts[2], epc=parts[0],
                                        allow_rebind=True)
                    cnt += 1
        return cnt
