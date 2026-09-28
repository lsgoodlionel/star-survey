"""考试端到端的共用零件（WP-09 / WP-10）。

两件事：

- `response_fields()`：把**题目代码**换算成答卷表的列名；
- 考场记录与答卷行的只读探针（`exam_attempt()` / `exam_submitdate()` /
  `exam_responses()`）。WP-09.2 之后"到点强制交卷"要分辨**谁收的卷**，而看这件事
  的驱动不止一个：`exam_timing.py` 正面验证三条收卷路径，`access_policy.py` 的
  限时场景也要据此区分"作答者自己交的"与"服务端强制收的"。
"""

from typing import Dict, List, Optional

#: 考场记录表（MjyExamAttemptStore）。只有带限时的问卷才会有行。
ATTEMPT_TABLE = "lime_mjyruntimepolicy_exam_attempt"


def response_fields(db, survey_id: int) -> Dict[str, str]:
    """题目代码 → 答卷表列名；多选的子题键是 `<父题代码>.<子题代码>`。

    引擎这一版按 `Q<qid>` 命名，多选的每个子题是 `Q<父题qid>_S<子题qid>`，
    多选的父题自己没有列。列名是引擎的内部约定，**推出来之后要对着真表核一遍**：
    名字不对的话表单里那几个字段根本不会被引擎接收，答卷是空的、判分随之全 0——
    看起来像"判分坏了"，其实是用例把字段名写错了。这个坑真踩过一次。
    """
    actual = {row[0] for row in db.rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'lime_responses_{}'".format(survey_id))}
    rows = db.rows(
        "SELECT qid, parent_qid, title FROM lime_questions WHERE sid = {}".format(survey_id))
    parents = {row[0]: row[2] for row in rows if row[1] == "0"}

    fields: Dict[str, str] = {}
    for qid, parent_qid, title in rows:
        if parent_qid == "0":
            # 多选题的父题自己没有列，答案分散在子题列里——这里只收真有列的。
            if "Q{}".format(qid) in actual:
                fields[title] = "Q{}".format(qid)
            continue
        name = "Q{}_S{}".format(parent_qid, qid)
        if name not in actual:
            raise RuntimeError("子题列 {} 不在答卷表里；实际列：{}".format(name, sorted(actual)))
        fields["{}.{}".format(parents[parent_qid], title)] = name
    if not fields:
        raise RuntimeError("一个列都没对上；实际列：{}".format(sorted(actual)))
    return fields


def exam_attempt(db, survey_id: int, token: str) -> Dict[str, str]:
    """考场记录里这位考生那一行；没有就返回空字典。

    答卷行号与截止时刻都以它为准，**不靠答卷表的下标去猜**——进场就会建行，
    下标对不上人。身份键就是插件的 `sessionKey()`：发了准考证时是 `token:<准考证>`。
    """
    rows = db.rows(
        "SELECT response_id, deadline_at, state FROM {} "
        "WHERE survey_id = {} AND session_key = 'token:{}'".format(ATTEMPT_TABLE, survey_id, token))
    if not rows:
        return {}
    # PostgreSQL 的 timestamp 带微秒后缀，统一截到秒，好跟答卷表的 submitdate 直接比。
    return {"response_id": rows[0][0], "deadline_at": rows[0][1][:19], "state": rows[0][2]}


def exam_attempt_count(db, survey_id: int) -> int:
    """这份问卷有几条考场记录。没有限时的问卷应当是 0：它根本不进监考。"""
    return int(db.value("SELECT COUNT(*) FROM {} WHERE survey_id = {}".format(
        ATTEMPT_TABLE, survey_id)) or 0)


def exam_submitdate(db, survey_id: int, response_id: Optional[str]) -> Optional[str]:
    """这一行答卷的交卷时刻；还没交卷返回 None。

    两个驱动打印 NULL 的方式不一样（mariadb 打 `NULL`，psql 打空串），在这里吃掉。
    """
    if response_id in (None, "", "NULL"):
        return None
    value = db.value(
        "SELECT submitdate FROM lime_responses_{} WHERE id = {}".format(survey_id, response_id))
    return None if value in ("", "NULL", None) else value[:19]


def exam_responses(db, survey_id: int) -> List[List[str]]:
    """答卷表里的 (行号, 交卷时刻)，按行号排序。"""
    return db.rows(
        "SELECT id, submitdate FROM lime_responses_{} ORDER BY id".format(survey_id))
