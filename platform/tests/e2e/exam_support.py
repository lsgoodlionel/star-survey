"""考试端到端的共用零件（WP-09 / WP-10）。

现在只有一件：把**题目代码**换算成答卷表的列名。两个驱动（exam_timing、
exam_grading）都要往表单里塞具体某道题的答案，而表单字段名就是答卷表的列名。
"""

from typing import Dict


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
