import type { EditableQuestion } from './model/definition';
import { ReadOnlyQuestion } from './ReadOnlyQuestion';

interface QuestionEditorProps {
  question: EditableQuestion;
  disabled: boolean;
  onChange(update: Partial<Pick<EditableQuestion, 'answers' | 'code' | 'mandatory' | 'other' | 'text'>>): void;
}

export function QuestionEditor({ question, disabled, onChange }: QuestionEditorProps) {
  if (!question.editable) return <ReadOnlyQuestion question={question} />;

  return (
    <div className="question-editor">
      <div className="editor-section-heading">
        <div>
          <p>题型 {question.type}</p>
          <h2>编辑题目</h2>
        </div>
      </div>
      <label>
        题目编码
        <input
          aria-label="题目编码"
          value={question.code}
          disabled={disabled}
          onChange={(event) => onChange({ code: event.target.value })}
        />
      </label>
      <label>
        题目文本
        <textarea
          aria-label="题目文本"
          rows={5}
          value={question.text}
          disabled={disabled}
          onChange={(event) => onChange({ text: event.target.value })}
        />
      </label>
      {question.type !== 'X' ? (
        <label className="editor-checkbox-row">
          <input
            type="checkbox"
            checked={question.mandatory ?? false}
            disabled={disabled}
            onChange={(event) => onChange({ mandatory: event.target.checked })}
          />
          必答题
        </label>
      ) : null}
      {question.answers ? (
        <fieldset disabled={disabled}>
          <legend>选项</legend>
          {question.answers.map((answer, index) => (
            <div className="editor-choice-row" key={`${index}-${answer.code}`}>
              <input
                aria-label={`选项 ${index + 1} 编码`}
                value={answer.code}
                onChange={(event) =>
                  onChange({
                    answers: question.answers?.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, code: event.target.value } : item,
                    ),
                  })
                }
              />
              <input
                aria-label={`选项 ${index + 1} 文本`}
                value={answer.text}
                onChange={(event) =>
                  onChange({
                    answers: question.answers?.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, text: event.target.value } : item,
                    ),
                  })
                }
              />
            </div>
          ))}
          <label className="editor-checkbox-row">
            <input
              type="checkbox"
              checked={question.other ?? false}
              onChange={(event) => onChange({ other: event.target.checked })}
            />
            允许填写其他答案
          </label>
        </fieldset>
      ) : null}
    </div>
  );
}
