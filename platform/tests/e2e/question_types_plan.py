"""question_types.py 的作答计划：每页的合法答案、只答必答题的空答路径，以及篡改用例。

列一律写成 (题目代码, aid, 尺度)，由驱动按绑定映射换成引擎字段名。排序题提交的是名次虚列
（aid 1…n），引擎把它们合成一列 JSON。
"""

from typing import Any, Dict, List, Tuple

Column = Tuple[str, str, int]

PAGES = ("选择题", "矩阵题", "排序、数值与上传", "填空与格式")
#: 每页用来判断「是否还停在本页」的题目。
MARKERS = ("QSINGLE", "QARR", "QNUM", "QMOBILE")

VALID_ID_CARD = "11010519491231002X"


def valid_pages() -> List[Dict[Column, str]]:
    return [
        {
            ("QSINGLE", "", 0): "-oth-", ("QSINGLE", "other", 0): "自填选项",
            ("QDROP", "", 0): "A2",
            ("QCOMMENT", "", 0): "A1", ("QCOMMENT", "comment", 0): "很好",
            ("QMULTI", "S1", 0): "Y", ("QMULTI", "S2", 0): "Y",
            ("QMULTIC", "S1", 0): "Y", ("QMULTIC", "S1comment", 0): "快",
            ("QYES", "", 0): "Y", ("QGENDER", "", 0): "F", ("QNPS", "", 0): "10",
            ("QSTAR", "", 0): "4", ("QIMG", "", 0): "A2",
        },
        {
            ("QARR", "R1", 0): "A1", ("QARR", "R2", 0): "A3",
            ("QCOL", "R1", 0): "A2", ("QCOL", "R2", 0): "A1",
            ("QA5", "R1", 0): "5", ("QA10", "R1", 0): "10", ("QYUN", "R1", 0): "U", ("QISD", "R1", 0): "D",
            ("QDUAL", "R1", 0): "L2", ("QDUAL", "R1", 1): "H1",
            ("QMNUM", "R1_C1", 0): "12", ("QMNUM", "R1_C2", 0): "3.5",
            ("QMNUM", "R2_C1", 0): "0", ("QMNUM", "R2_C2", 0): "7",
            ("QMTXT", "R1_C1", 0): "销售", ("QMTXT", "R1_C2", 0): "经理",
            ("QMTXT", "R2_C1", 0): "研发", ("QMTXT", "R2_C2", 0): "工程师",
        },
        {
            ("QRANK", "1", 0): "I3", ("QRANK", "2", 0): "I1", ("QRANK", "3", 0): "I2",
            ("QNUM", "", 0): "36",
            ("QALLOC", "P1", 0): "60", ("QALLOC", "P2", 0): "30", ("QALLOC", "P3", 0): "10",
            ("QSLIDE", "V1", 0): "7",
            ("QDATE", "", 0): "2024-05-06",
        },
        {
            ("QSHORT", "", 0): "小明",
            ("QMOBILE", "", 0): "13800138000",
            ("QIDCARD", "", 0): VALID_ID_CARD,
            ("QUSCC", "", 0): "91350100M000100Y43",
            ("QEMAIL", "", 0): "a.b@example.cn",
            ("QPOST", "", 0): "100086",
            ("QLONG", "", 0): "整体满意",
            ("QHUGE", "", 0): "很长的说明 <b>加粗</b> & 符号",
            ("QMTEXT", "F1", 0): "张三", ("QMTEXT", "F2", 0): "某单位",
        },
    ]


def blank_pages() -> List[Dict[Column, str]]:
    """只答必答题与有最少选择数的多选；其余一概不填。"""
    return [
        {("QSINGLE", "", 0): "A1", ("QMULTI", "S3", 0): "Y"},
        {},
        {},
        {("QMOBILE", "", 0): "13900139000"},
    ]


def _tamper(label: str, page: int, answers: Dict[Column, str], **extra: Any) -> Dict[str, Any]:
    payload = {"label": label, "page": page, "answers": answers}
    payload.update(extra)
    return payload


TAMPERS = (
    # 第 1 页：选择题
    _tamper("single choice outside the options", 0, {("QSINGLE", "", 0): "A9"}),
    # 互斥项＋其他项，并把所有 relevance* 影子字段写成 1：引擎在服务端重算子题相关性，
    # 把被互斥掉的选项置 NULL（deletenonvalues），所以不是「留在本页」而是「入库时已被纠正」。
    _tamper("exclusive option plus another, every relevance* shadow forced to 1", 0,
            {("QMULTI", "SNONE", 0): "Y", ("QMULTI", "S1", 0): "Y", ("QMULTI", "S2", 0): ""}, shadow="all-relevant",
            expect="normalize", stored={("QMULTI", "SNONE", 0): "Y", ("QMULTI", "S1", 0): None,
                                        ("QMULTI", "S2", 0): None, ("QMULTI", "S3", 0): None}),
    # 在第一页直接提交整份问卷：后面几页的必答题（手机号）从没见过，引擎不能收卷。
    _tamper("submitting the whole survey from page 1", 0, {}, move="movesubmit", marker=None),
    _tamper("three options over max_answers=2", 0, {("QMULTI", "S3", 0): "Y"}),
    _tamper("yes/no outside Y/N", 0, {("QYES", "", 0): "X"}),
    _tamper("NPS 11", 0, {("QNPS", "", 0): "11"}),
    _tamper("five-point 6", 0, {("QSTAR", "", 0): "6"}),
    _tamper("gender outside F/M", 0, {("QGENDER", "", 0): "Z"}),
    # 第 2 页：矩阵
    _tamper("array row outside the options", 1, {("QARR", "R1", 0): "A9"}),
    _tamper("5-point array 7", 1, {("QA5", "R1", 0): "7"}),
    _tamper("yes/uncertain/no array Z", 1, {("QYUN", "R1", 0): "Z"}),
    _tamper("dual scale: scale-0 code on scale 1", 1, {("QDUAL", "R1", 1): "L1"}),
    _tamper("number matrix non-numeric", 1, {("QMNUM", "R1_C1", 0): "abc"}),
    # 第 3 页：排序、数值
    _tamper("ranking the same item twice", 2, {("QRANK", "1", 0): "I1", ("QRANK", "2", 0): "I1"}),
    _tamper("ranking an unknown item", 2, {("QRANK", "1", 0): "I9"}),
    _tamper("number above max 120", 2, {("QNUM", "", 0): "121"}),
    _tamper("decimal where integers only", 2, {("QNUM", "", 0): "3.5"}),
    _tamper("allocation summing to 95", 2, {("QALLOC", "P3", 0): "5"}),
    _tamper("negative allocation", 2, {("QALLOC", "P1", 0): "-10", ("QALLOC", "P2", 0): "100"}),
    _tamper("date before date_min", 2, {("QDATE", "", 0): "2019-12-31"}),
    _tamper("non-existent date", 2, {("QDATE", "", 0): "2021-02-30"}),
    # 第 4 页：填空与中国本地化格式（最后一页，提交用 movesubmit）
    _tamper("mobile with a 2nd digit of 2", 3, {("QMOBILE", "", 0): "12800138000"}),
    _tamper("mandatory mobile claimed hidden via relevance shadow", 3, {("QMOBILE", "", 0): "12800138001"},
            shadow="claim-hidden"),
    _tamper("ID card bad checksum, java shadow holding a valid one", 3,
            {("QIDCARD", "", 0): "110105194912310021"}, shadow="java-valid",
            java={("QIDCARD", "", 0): VALID_ID_CARD}),
    _tamper("ID card impossible birth date", 3, {("QIDCARD", "", 0): "11010519491331002X"}),
    _tamper("USCC bad checksum", 3, {("QUSCC", "", 0): "91350100M000100Y44"}),
    _tamper("postcode with 5 digits", 3, {("QPOST", "", 0): "10008"}),
    _tamper("email without a domain dot", 3, {("QEMAIL", "", 0): "a@b"}),
    _tamper("email over the v2 validation length", 3, {("QEMAIL", "", 0): "a" * 40 + "@example.cn"}),
    _tamper("short text over maxLength 10", 3, {("QSHORT", "", 0): "一二三四五六七八九十十"}),
)
