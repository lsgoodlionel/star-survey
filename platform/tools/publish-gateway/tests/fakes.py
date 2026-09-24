"""假的 RemoteControl 传输层。

单元测试不依赖运行中的引擎：这里按 LimeSurvey 7.1.2 的应答形状模拟
RemoteControl，包括它那几种不一致的错误返回。真引擎上的行为由
platform/tests/e2e/publish_gateway.py 验证。
"""

import json

from pubgw.qtypes import expected_rows


#: 参与者表的列（``Token`` 模型的 tableSchema）。引擎用 array_intersect_key 只留这些，
#: 平台传的别的键不会回到回执里。
TOKEN_COLUMNS = frozenset({
    "tid", "participant_id", "firstname", "lastname", "email", "emailstatus",
    "token", "language", "blacklisted", "sent", "remindersent", "remindercount",
    "completed", "usesleft", "validfrom", "validuntil",
})


class FakeEngine:
    """按一份定义模拟引擎，可以通过开关注入各种故障。"""

    def __init__(
        self,
        definition=None,
        rename=None,
        theme_override=None,
        inherited_settings=None,
        fail_activate=False,
        fail_delete=False,
        drop_fields=(),
        sticky_settings=(),
        participant_errors=False,
        participant_tokens=None,
        participants_returned=None,
        import_result=None,
    ):
        self.definition = definition
        self.rename = dict(rename or {})
        self.theme_override = dict(theme_override or {})
        self.inherited_settings = dict(inherited_settings or {})
        self.fail_activate = fail_activate
        self.fail_delete = fail_delete
        self.drop_fields = tuple(drop_fields)
        self.sticky_settings = tuple(sticky_settings)
        self.participant_errors = participant_errors
        #: 逐条指定引擎生成的 token（空串＝generateToken 没生成出来）。
        self.participant_tokens = participant_tokens
        #: 只返回前 n 条，用来钉住"返回列表比提交的短"。
        self.participants_returned = participants_returned
        self.import_result = import_result
        self.calls = []
        self.surveys = {}
        self.deleted = []
        self.participants = {}
        self.applied_settings = {}
        self._next_sid = 511001

    # ------------------------------------------------------------ 传输

    def transport(self, payload: bytes) -> bytes:
        request = json.loads(payload.decode("utf-8"))
        method = request["method"]
        params = request["params"]
        self.calls.append((method, params))
        handler = getattr(self, "_" + method, None)
        if handler is None:
            return self._envelope({"status": "Method not supported", "error_code": 1})
        return self._envelope(handler(*params))

    @staticmethod
    def _envelope(result):
        return json.dumps({"id": 1, "result": result, "error": None}).encode("utf-8")

    def methods(self):
        return [method for method, _ in self.calls]

    # ------------------------------------------------------- RPC 方法

    def _get_session_key(self, username, password, plugin=None):
        if username != "admin":
            return {"status": "Invalid user name or password"}
        return "fake-session-key"

    def _release_session_key(self, key):
        return "OK"

    def _import_survey(self, key, data, kind, name=None):
        if self.import_result is not None:
            return self.import_result
        sid = self._next_sid
        self._next_sid += 1
        self.surveys[sid] = {"active": "N", "lss": data}
        return sid

    def _activate_survey(self, key, sid):
        if self.fail_activate:
            return {"status": "Error: Consistency check failed", "error_code": 15}
        self.surveys[sid]["active"] = "Y"
        return {"status": "OK"}

    def _activate_tokens(self, key, sid, attributes=None):
        self.surveys[sid]["tokens"] = True
        return {"status": "OK"}

    def _add_participants(self, key, sid, participants, create_token=True):
        # 引擎逐个处理参与者，失败的那一条多一个 errors 键
        # （remotecontrol_handle.php:2130-2134），整体仍然返回一个列表。
        if self.participant_errors:
            return [dict(entry, errors={"email": ["Email address is not valid"]}) for entry in participants]
        self.participants[sid] = participants
        # 成功的条目被整条换成 token 行属性：平台传的非列字段已经被
        # array_intersect_key 丢掉，且按引用原地替换，所以与提交同序。
        rows = []
        for index, entry in enumerate(participants):
            row = {key: value for key, value in entry.items() if key in TOKEN_COLUMNS}
            row["tid"] = index + 1
            if create_token:
                row["token"] = self._token_for(index)
            rows.append(row)
        if self.participants_returned is not None:
            rows = rows[: self.participants_returned]
        return rows

    def _token_for(self, index):
        if self.participant_tokens is None:
            return "tok{}".format(index + 1)
        return self.participant_tokens[index]

    def _delete_survey(self, key, sid):
        if self.fail_delete:
            return {"status": "No permission", "error_code": 2}
        self.surveys.pop(sid, None)
        self.deleted.append(sid)
        return {"status": "OK"}

    def _get_survey_properties(self, key, sid, properties=None):
        values = dict(self.definition.settings)
        values["template"] = self.definition.theme
        values.update(self.inherited_settings)
        if sid in self.surveys:
            values["active"] = self.surveys[sid]["active"]
        for name in self.sticky_settings:
            values[name] = "STICKY"
        values.update({k: v for k, v in self.applied_settings.get(sid, {}).items()
                       if k not in self.sticky_settings})
        return values

    def _set_survey_properties(self, key, sid, properties):
        applied = dict(self.applied_settings.get(sid, {}))
        applied.update(properties)
        self.applied_settings[sid] = applied
        return {name: True for name in properties}

    def _list_questions(self, key, sid, gid=None, language=None):
        rows = []
        qid = 100
        for question in self.definition.questions():
            qid += 1
            rows.append(
                {
                    "qid": qid,
                    "parent_qid": 0,
                    "sid": sid,
                    "type": question.type,
                    "title": self.rename.get(question.code, question.code),
                    "question_theme_name": self.theme_override.get(
                        question.code, question.theme or "core"
                    ),
                }
            )
        return rows

    def _get_fieldmap(self, key, sid, language=None):
        rows = {
            "id": {"fieldname": "id", "type": "id", "sid": sid, "gid": "", "qid": ""},
            "submitdate": {"fieldname": "submitdate", "type": "submitdate", "sid": sid, "gid": "", "qid": ""},
        }
        qid = 100
        for question in self.definition.questions():
            qid += 1
            for index, (code, aid, scale) in enumerate(expected_rows(question)):
                if (question.code, aid, scale) in self.drop_fields:
                    continue
                fieldname = "Q{}_{}".format(qid, index)
                row = {
                    "fieldname": fieldname,
                    "type": question.type,
                    "sid": sid,
                    "gid": 1,
                    "qid": qid,
                    "aid": aid,
                    "title": self.rename.get(code, code),
                }
                if question.type == "1":
                    row["scale_id"] = scale
                rows[fieldname] = row
        return rows
