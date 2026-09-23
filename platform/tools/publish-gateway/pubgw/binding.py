"""绑定记录：发布成功后平台必须存下来的那份东西。

它回答三个问题：

- 这份定义现在跑在**哪个**引擎实例的**哪个** sid 上；
- 平台的题目 UUID 对应引擎的哪个题目代码、哪些答卷列
  （三段映射，第三段每次激活都要重建，ADR 0005 决定 3）；
- 当时的结构指纹是什么，用来做后续的漂移比对。

记录本身只是数据，没有任何行为：平台侧怎么存（问卷版本表、对象存储）
不由网关决定。
"""

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from .fieldmap import FieldBinding, QuestionBinding


@dataclass(frozen=True)
class BindingRecord:
    engine_instance: str
    survey_id: int
    definition_uuid: str
    compiler_version: str
    fingerprint_version: str
    fingerprint: str
    language: str
    published_at: str
    questions: Tuple[QuestionBinding, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engineInstance": self.engine_instance,
            "surveyId": self.survey_id,
            "definitionUuid": self.definition_uuid,
            "compilerVersion": self.compiler_version,
            "fingerprintVersion": self.fingerprint_version,
            "fingerprint": self.fingerprint,
            "language": self.language,
            "publishedAt": self.published_at,
            "questions": [question.to_dict() for question in self.questions],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "BindingRecord":
        return cls(
            engine_instance=str(payload["engineInstance"]),
            survey_id=int(payload["surveyId"]),
            definition_uuid=str(payload["definitionUuid"]),
            compiler_version=str(payload["compilerVersion"]),
            fingerprint_version=str(payload["fingerprintVersion"]),
            fingerprint=str(payload["fingerprint"]),
            language=str(payload["language"]),
            published_at=str(payload["publishedAt"]),
            questions=tuple(_question(entry) for entry in payload.get("questions") or []),
        )

    def field_owners(self) -> Dict[str, Tuple[str, str]]:
        """字段名 → (题目 UUID, 当时的题目代码)。漂移检查用它做精确配对。"""
        owners = {}
        for question in self.questions:
            for field in question.fields:
                owners[field.fieldname] = (question.uuid, question.code)
        return owners


def _question(payload: Dict[str, Any]) -> QuestionBinding:
    return QuestionBinding(
        uuid=str(payload["uuid"]),
        code=str(payload["code"]),
        type=str(payload["type"]),
        fields=tuple(
            FieldBinding(
                fieldname=str(field["fieldname"]),
                aid=str(field["aid"]),
                scale=int(field["scale"]),
            )
            for field in payload.get("fields") or []
        ),
        side_table=payload.get("sideTable"),
    )
