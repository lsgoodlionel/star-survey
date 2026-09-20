"""测试共用的定义与假 get_fieldmap 数据。"""

from pubgw.model import SurveyDefinition
from pubgw.validate import REQUIRED_EXPLICIT_SETTINGS


def explicit_settings(**overrides):
    settings = {name: "N" for name in REQUIRED_EXPLICIT_SETTINGS}
    settings.update({"format": "G", "questionindex": "0", "datestamp": "Y"})
    settings.update(overrides)
    return settings


def sample_payload():
    """两个题组、四道题：单选（带其他）、多选、数组双尺度、短文本。"""
    return {
        "definitionVersion": 1,
        "uuid": "def-0001",
        "title": "P0 发布网关样例",
        "language": "en",
        "theme": "fruity_twentythree",
        "settings": explicit_settings(),
        "groups": [
            {
                "uuid": "grp-1",
                "title": "基本情况",
                "questions": [
                    {
                        "uuid": "q-single",
                        "code": "QSINGLE",
                        "type": "L",
                        "text": "选一个",
                        "mandatory": True,
                        "other": True,
                        "answers": [
                            {"code": "A1", "text": "甲"},
                            {"code": "A2", "text": "乙"},
                        ],
                    },
                    {
                        "uuid": "q-text",
                        "code": "QTEXT",
                        "type": "S",
                        "text": "写点什么",
                        "attributes": {"maximum_chars": "200"},
                    },
                ],
            },
            {
                "uuid": "grp-2",
                "title": "评价",
                "questions": [
                    {
                        "uuid": "q-multi",
                        "code": "QMULTI",
                        "type": "M",
                        "text": "多选",
                        "subquestions": [
                            {"uuid": "sq-m1", "code": "SQ001", "text": "甲"},
                            {"uuid": "sq-m2", "code": "SQ002", "text": "乙"},
                        ],
                    },
                    {
                        "uuid": "q-dual",
                        "code": "QDUAL",
                        "type": "1",
                        "text": "双尺度数组",
                        "subquestions": [{"uuid": "sq-d1", "code": "SQ001", "text": "行一"}],
                        "answers": [
                            {"code": "L1", "text": "左一", "scale": 0},
                            {"code": "R1", "text": "右一", "scale": 1},
                        ],
                    },
                ],
            },
        ],
    }


def sample_definition():
    return SurveyDefinition.from_dict(sample_payload())


def meta_rows():
    """答卷表固有列。get_fieldmap 把它们排在最前面，且没有 qid。"""
    return {
        "id": {"fieldname": "id", "type": "id", "sid": 1, "gid": "", "qid": ""},
        "submitdate": {"fieldname": "submitdate", "type": "submitdate", "sid": 1, "gid": "", "qid": ""},
        "lastpage": {"fieldname": "lastpage", "type": "lastpage", "sid": 1, "gid": "", "qid": ""},
        "startlanguage": {"fieldname": "startlanguage", "type": "startlanguage", "sid": 1, "gid": "", "qid": ""},
        "seed": {"fieldname": "seed", "type": "seed", "sid": 1, "gid": "", "qid": ""},
    }


def _row(fieldname, qtype, title, aid, qid, scale=None):
    row = {
        "fieldname": fieldname,
        "type": qtype,
        "sid": 1,
        "gid": 1,
        "qid": qid,
        "aid": aid,
        "title": title,
    }
    if scale is not None:
        row["scale_id"] = scale
    return {fieldname: row}


def fieldmap_for(definition, renamed=None, qid_base=100):
    """按定义生成一份「引擎本该返回」的 get_fieldmap，供单元测试使用。

    fieldname 用 Q<qid>_<序号> 这种形状，只要在一次激活内稳定即可——
    单元测试关心的是代码与尺度，不是字段名的真实拼法。
    """
    from pubgw.qtypes import expected_rows

    renamed = renamed or {}
    rows = meta_rows()
    qid = qid_base
    for question in definition.questions():
        qid += 1
        for index, (_, aid, scale) in enumerate(expected_rows(question)):
            fieldname = "Q{}_{}".format(qid, index)
            rows.update(
                _row(
                    fieldname,
                    question.type,
                    renamed.get(question.code, question.code),
                    aid,
                    qid,
                    scale if question.type == "1" else None,
                )
            )
    return rows
